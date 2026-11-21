#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""InlineManager — Central coordinator for the TeleSender inline system."""

import asyncio
import logging
import os
from typing import Any, Callable, Dict, List, Optional, Union

from telethon import TelegramClient
from telethon.sessions import StringSession

from src.config.config import config
from src.services.translator import lang
from src.services.user_agent import describe as describe_user_agent
from src.web.sessions import create_bot_login_code
from .bot_api import BotApiClient
from .events import EventRouter
from .form import FormManager
from .token_obtainer import TokenObtainer
from .types import InlineCall, InlineForm

logger = logging.getLogger("telesender.inline.manager")


class InlineManager:
    """Provides the ``self.inline`` interface accessible to all TeleSender plugins.

    Architecture:
    - User account runs on main Telethon client
    - Inline bot runs concurrently on a dedicated secondary Telethon client
    - Plugins call ``await self.inline.form(...)`` to display interactive buttons
    - Callbacks, TTL cleanup, and permission checks are handled automatically
    """

    def __init__(
        self,
        client: TelegramClient,
        db: Any = None,
        owner_id: int = 0,
        plugin_registry: Any = None,
        allowed_users: Optional[List[int]] = None,
    ):
        self._client = client
        self._db = db
        self._owner_id = owner_id
        self.plugin_registry = plugin_registry
        self._allowed_users = allowed_users or []

        self.bot_client: Optional[TelegramClient] = None
        self.bot_api: Optional[BotApiClient] = None
        self.bot_username: Optional[str] = None
        self.bot_id: Optional[int] = None
        self.form_manager: Optional[FormManager] = None
        self._event_router: Optional[EventRouter] = None
        self._ready = False

    async def init(self) -> bool:
        """Obtain bot token, connect bot client, and initialize subsystems."""
        try:
            obtainer = TokenObtainer(self._client)
            token = await obtainer.obtain_token()
            if not token:
                logger.error("Could not obtain a bot token — inline system disabled")
                return False

            session_dir = os.path.dirname(config.session_path) or "data"
            os.makedirs(session_dir, exist_ok=True)
            bot_session_file = os.path.join(session_dir, "inline_bot")

            # Initialize secondary Telethon client for the bot
            self.bot_client = TelegramClient(
                bot_session_file,
                config.api_id,
                config.api_hash,
            )

            await self.bot_client.start(bot_token=token)

            # Initialize HTTP Bot API client
            self.bot_api = BotApiClient(token)

            if not self._owner_id:
                try:
                    me_user = await self._client.get_me()
                    if me_user:
                        self._owner_id = me_user.id
                except Exception:
                    pass

            me = await self.bot_client.get_me()
            self.bot_username = me.username
            self.bot_id = me.id
            logger.info("Inline bot connected: @%s (id=%s)", self.bot_username, self.bot_id)

            # Initialize form manager & event router
            self.form_manager = FormManager(self.bot_client)
            self._event_router = EventRouter(self, self.bot_client)

            # Register callback handler on user client to catch inline button presses
            # (Telegram routes inline message callbacks to the user session, not the bot session)
            self._register_user_callback_handler()

            # Start TTL cleanup task
            self.form_manager.start_cleanup_loop(interval=60.0)

            # Wake up the bot by sending /start from the user client.
            # Telegram requires a user to write to the bot first before it
            # can respond — this "priming" message activates the bot session.
            # Done after all subsystems are initialized so the bot is ready.
            try:
                await asyncio.sleep(1.0)
                wake_msg = await self._client.send_message(self.bot_username, "/start")
                await asyncio.sleep(1.0)
                await self._client.delete_messages(self.bot_username, wake_msg)
            except Exception as e:
                logger.debug("Could not send wake-up /start to bot: %s", e)

            # Ensure Telegram Login Widget domain and Mini App settings are configured
            # in BotFather on every restart (domain for Login Widget, menu button,
            # Main Mini App configuration and splash screen).
            try:
                obtainer = TokenObtainer(self._client)
                await obtainer.ensure_login_widget_domain(self.bot_username)
                # Menu button is set via Bot API below. Calling /setmenubutton here would
                # send the web URL into BotFather *before* Main Mini App creation, which
                # breaks the required Enable Mini App -> URL -> back-navigation sequence.
                await obtainer.ensure_mini_app_splash_screen(self.bot_username)
            except Exception as e:
                logger.warning("Could not configure Telegram Login Widget/Mini App in BotFather: %s", e)

            # Configure bot menu button to open the web interface Mini App on start
            # (Bot API fallback/ensure).
            try:
                if self.bot_api and getattr(config, "web_enabled", False):
                    web_url = (config.web_url or "").rstrip("/")
                    menu_text = lang.get("inline.open_web", "Open web interface")
                    await self.bot_api.set_chat_menu_button(web_app_url=web_url, text=menu_text[:64])
            except Exception as e:
                logger.debug("Could not set chat menu button: %s", e)

            self._ready = True
            return True

        except Exception as e:
            logger.error("Failed to initialize inline bot system: %s", e, exc_info=True)
            return False

    @property
    def is_ready(self) -> bool:
        """Whether the inline bot is connected and ready."""
        return self._ready

    async def form(
        self,
        text: str,
        message: Any,
        reply_markup: Optional[Any] = None,
        force_me: bool = True,
        ttl: Optional[float] = None,
        disable_security: bool = False,
        always_allow: Optional[List[int]] = None,
        reply_to: Optional[Any] = None,
        silent: bool = False,
    ) -> Optional[InlineForm]:
        """Send an interactive inline form using the inline query invoke pattern.

        Instead of sending a direct message from the bot into the user's private chat,
        this queries the bot via inline_query and clicks the result in the current chat,
        then deletes the original command message so it seamlessly replaces it.
        """
        if not self._ready or not self.form_manager:
            logger.warning("Inline system not ready — cannot send form")
            return None

        chat_id = self._resolve_chat_id(message)
        owner_id = self._resolve_sender_id(message)
        target_entity = getattr(message, "input_chat", None) or getattr(message, "chat", None) or chat_id

        reply_to_id = None
        if reply_to is not None:
            reply_to_id = getattr(reply_to, "id", reply_to)
        elif hasattr(message, "reply_to_msg_id") and message.reply_to_msg_id:
            reply_to_id = message.reply_to_msg_id

        # Delete triggering command message immediately without intermediate placeholder
        if hasattr(message, "delete"):
            try:
                await message.delete()
            except Exception:
                pass

        # 1. Register form in memory for the inline_query handler
        form = self.form_manager.register_form(
            text=text,
            chat_id=chat_id,
            reply_markup=reply_markup,
            owner_id=owner_id,
            force_me=force_me,
            ttl=ttl,
            disable_security=disable_security,
            always_allow=always_allow or [],
        )

        # 2. Query bot via user client and post result into current chat
        sent_message = None
        try:
            results = await self._client.inline_query(self.bot_username, form.form_id)
            if results:
                sent_message = await results[0].click(entity=target_entity, reply_to=reply_to_id)
                if sent_message:
                    form.message_id = sent_message.id
                    # Telethon click() on an inline result returns a message with
                    # inline_message_id stored on the result object
                    inline_msg_id = getattr(results[0], "inline_message_id", None)
                    if inline_msg_id:
                        form.inline_message_id = inline_msg_id
        except Exception as e:
            logger.warning("Inline query dispatch failed (%s), falling back to bot send_message", e)

        # 3. Fallback if inline query was not permitted in chat
        if not sent_message:
            try:
                buttons = self.form_manager.build_buttons(None, form)
                sent_message = await self.bot_client.send_message(
                    chat_id,
                    text,
                    buttons=buttons,
                    parse_mode="html",
                    reply_to=reply_to_id,
                )
                if sent_message:
                    form.message_id = sent_message.id
            except Exception as e:
                logger.error("Failed to send form via fallback: %s", e)

        return form

    @staticmethod
    def _resolve_chat_id(message: Any) -> Union[int, str]:
        """Extract chat_id from Telethon message or event."""
        if hasattr(message, "chat_id") and message.chat_id is not None:
            return message.chat_id
        if hasattr(message, "peer_id") and message.peer_id is not None:
            peer = message.peer_id
            if hasattr(peer, "user_id"):
                return peer.user_id
            if hasattr(peer, "chat_id"):
                return -peer.chat_id
            if hasattr(peer, "channel_id"):
                return int(f"-100{peer.channel_id}")
        if hasattr(message, "to_id"):
            return getattr(message.to_id, "user_id", 0)
        return 0

    @staticmethod
    def _resolve_sender_id(message: Any) -> int:
        """Extract sender user ID from message or event."""
        if hasattr(message, "sender_id") and message.sender_id:
            return message.sender_id
        if hasattr(message, "from_id") and message.from_id:
            if hasattr(message.from_id, "user_id"):
                return message.from_id.user_id
        return 0

    async def stop(self) -> None:
        """Gracefully disconnect the inline bot and cleanup."""
        self._ready = False
        logger.info("Stopping inline manager...")

        if self.form_manager:
            await self.form_manager.stop()

        if self.bot_api:
            await self.bot_api.close()

        if self.bot_client and self.bot_client.is_connected():
            try:
                await self.bot_client.disconnect()
            except Exception as e:
                logger.debug("Error disconnecting bot client: %s", e)

        logger.info("Inline manager stopped")

    def _register_user_callback_handler(self) -> None:
        """Register a CallbackQuery handler on the user client.

        Telegram routes inline message button presses to the user session that
        created the inline message, not to the bot session. Without this handler,
        button clicks on inline messages (sent via inline_query) are never
        received by the bot.
        """
        from telethon import events

        @self._client.on(events.CallbackQuery)
        async def on_user_callback(event):
            try:
                raw_data = event.data.decode("utf-8", errors="ignore") if event.data else ""

                # Check if this is a 2FA callback query
                if raw_data.startswith("2fa:"):
                    parts = raw_data.split(":")
                    if len(parts) >= 3:
                        await self.handle_2fa_callback(event, parts[1], parts[2])
                    return

                # Open web interface button (inline callbacks may arrive on user client).
                if raw_data == "web:open":
                    from telethon import Button
                    from telethon.tl import types as tl_types
                    sender_id = event.sender_id
                    base_url = (config.web_url or "").rstrip("/")
                    is_allowed = sender_id is not None and int(sender_id) in (config.allowed_users or [])

                    def build_open_button(url: str):
                        if (url or "").lower().startswith("https://"):
                            return tl_types.KeyboardInlineButton(
                                lang.get("inline.open_web", "Open web interface"),
                                tl_types.InlineButtonTypeWebView(url),
                            )
                        return Button.url(lang.get("inline.open_web", "Open web interface"), url)

                    if is_allowed:
                        code = create_bot_login_code(int(sender_id))
                        login_url = f"{base_url}/auth/bot-login?code={code}"
                        try:
                            await event.answer(lang.get("inline.web_login_ready", "Authorized. Tap to open."), alert=False)
                        except Exception:
                            pass
                        try:
                            await event.edit(buttons=[[build_open_button(login_url)]])
                        except Exception:
                            try:
                                await event.edit(buttons=[[Button.url(lang.get("inline.open_web", "Open web interface"), login_url)]])
                            except Exception:
                                pass
                    else:
                        login_url = f"{base_url}/login" if base_url else "/login"
                        try:
                            await event.answer(lang.get("inline.web_login_password", "Open login page."), alert=False)
                        except Exception:
                            pass
                        try:
                            await event.edit(buttons=[[build_open_button(login_url)]])
                        except Exception:
                            try:
                                await event.edit(buttons=[[Button.url(lang.get("inline.open_web", "Open web interface"), login_url)]])
                            except Exception:
                                pass
                    return

                if ":" not in raw_data:
                    return

                form_id, button_id = raw_data.split(":", 1)
                form = self.form_manager.get_form(form_id)

                if form is None:
                    await event.answer(lang.get("inline.form_expired", "This form has expired or was removed"), alert=True)
                    return

                # Capture inline_message_id from the callback event
                inline_message_id = getattr(event, "inline_message_id", None)
                if inline_message_id and not form.inline_message_id:
                    form.inline_message_id = inline_message_id
                    logger.debug(
                        "Captured inline_message_id %s from user callback for form %s",
                        inline_message_id,
                        form.form_id,
                    )

                # Security verification
                if not self._check_user_access(event.sender_id, form):
                    await event.answer(lang.get("inline.access_denied", "Access denied"), alert=True)
                    return

                button = form.find_button(button_id)
                if button is None:
                    await event.answer(lang.get("inline.button_not_found", "Button not found"), alert=True)
                    return

                from .types import InlineCall
                call = InlineCall(
                    event=event,
                    form=form,
                    button=button,
                    manager=self,
                )

                # Built-in actions
                if button.action == "close":
                    await call.delete()
                    await event.answer()
                    return

                if button.action == "unload":
                    await call.unload()
                    await event.answer(lang.get("inline.form_unloaded", "Form unloaded"))
                    return

                if button.action == "answer":
                    alert_text = button.message or button.text
                    await event.answer(alert_text, alert=button.show_alert)
                    return

                # Custom plugin callback
                if button.callback:
                    args = button.args or []
                    kwargs = button.kwargs or {}
                    await button.callback(call, *args, **kwargs)
                else:
                    await event.answer()

            except Exception as e:
                logger.error("Error handling user callback query: %s", e, exc_info=True)
                try:
                    await event.answer(lang.get("inline.error_processing", "Error processing request"), alert=True)
                except Exception:
                    pass

    async def send_2fa_request(self, req: Any) -> bool:
        """Send an interactive 2FA confirmation prompt to the account owner via the inline bot."""
        if not self._ready or not self.bot_client:
            logger.warning("Inline bot not ready — cannot send 2FA request")
            return False

        owner_id = self._owner_id or config.my_user_id
        if not owner_id:
            try:
                me = await self._client.get_me()
                if me:
                    owner_id = me.id
                    self._owner_id = me.id
            except Exception:
                pass

        if not owner_id:
            logger.error("Owner ID unknown — cannot send 2FA request")
            return False

        from datetime import datetime
        from telethon import Button  # noqa: F401 (kept for clarity; Button used below)

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        purpose = getattr(req, "purpose", "login")
        if purpose == "disable_2fa":
            title = lang.get("inline.disable_2fa_title", "TeleSender Web — Disable 2FA")
            prompt_line = lang.get("inline.disable_2fa_prompt", "Do you confirm disabling two-factor authentication?")
        else:
            title = lang.get("inline.auth_request_title", "TeleSender Web — Login Request")
            prompt_line = lang.get("inline.auth_request_prompt", "Do you allow this login?")
        time_line = lang.get("inline.auth_request_time", "Time: {time}").format(time=now_str)
        ip_line = lang.get("inline.auth_request_ip", "IP: {ip}").format(ip=req.ip)
        browser_line = lang.get("inline.auth_request_browser", "Browser: {browser}").format(
            browser=describe_user_agent(
                req.user_agent,
                lang.get("inline.ua_unknown", "unknown"),
                hints=getattr(req, "hints", None),
                hidden_label=lang.get("inline.ua_device_hidden", "model hidden"),
            )
        )

        text = f"<b>{title}</b>\n\n{time_line}\n{ip_line}\n{browser_line}\n\n{prompt_line}"

        allow_text = lang.get("inline.auth_button_allow", "Confirm")
        deny_text = lang.get("inline.auth_button_deny", "Reject")

        buttons = [
            [
                Button.inline(allow_text, data=f"2fa:{req.request_id}:allow".encode("utf-8")),
                Button.inline(deny_text, data=f"2fa:{req.request_id}:deny".encode("utf-8")),
            ]
        ]

        try:
            msg = await self.bot_client.send_message(
                owner_id,
                text,
                buttons=buttons,
                parse_mode="html",
            )
            req.chat_id = owner_id
            req.message_id = msg.id
            return True
        except Exception as e:
            logger.error("Failed to send 2FA message to owner %s: %s", owner_id, e)
            return False

    async def handle_2fa_callback(self, event: Any, req_id: str, action: str) -> None:
        """Handle 2FA button presses (confirm / deny)."""
        from datetime import datetime
        from src.web.two_factor import two_factor_manager, save_2fa_setting

        sender_id = event.sender_id
        owner_id = self._owner_id or config.my_user_id
        if sender_id != owner_id and sender_id not in config.allowed_users:
            await event.answer(lang.get("inline.access_denied", "Access denied"), alert=True)
            return

        req = two_factor_manager.get_request(req_id)
        if not req or req.is_expired:
            await event.answer(lang.get("inline.auth_expired", "Request has expired"), alert=True)
            try:
                await event.edit(lang.get("inline.auth_expired", "Request has expired"), buttons=None)
            except Exception:
                pass
            return

        now_str = datetime.now().strftime("%H:%M:%S")
        purpose = getattr(req, "purpose", "login")
        disable_2fa = purpose == "disable_2fa"

        if action == "allow":
            if disable_2fa:
                try:
                    save_2fa_setting(False)
                except Exception as e:
                    logger.error("Failed to disable web 2FA: %s", e)
                    await event.answer(
                        lang.get("inline.disable_2fa_failed", "Failed to disable 2FA"),
                        alert=True,
                    )
                    return

            two_factor_manager.resolve_request(req_id, "approved")
            if disable_2fa:
                await event.answer(lang.get("inline.disable_2fa_approved_popup", "2FA disabled!"))
                title = lang.get("inline.disable_2fa_title", "TeleSender Web — Disable 2FA")
                done = lang.get("inline.disable_2fa_approved", "2FA disabled ({time})").format(time=now_str)
            else:
                await event.answer(lang.get("inline.auth_approved_popup", "Login approved!"))
                title = lang.get("inline.auth_request_title", "TeleSender Web — Login Request")
                done = lang.get("inline.auth_approved", "Login approved ({time})").format(time=now_str)
            approved_text = f"<b>{title}</b>\n\n{done}"
            try:
                await event.edit(approved_text, buttons=None, parse_mode="html")
            except Exception as e:
                logger.debug("Failed to edit 2FA message: %s", e)
        else:
            two_factor_manager.resolve_request(req_id, "rejected")
            if disable_2fa:
                await event.answer(lang.get("inline.disable_2fa_rejected_popup", "2FA disable rejected!"))
                title = lang.get("inline.disable_2fa_title", "TeleSender Web — Disable 2FA")
                done = lang.get("inline.disable_2fa_rejected", "2FA disable rejected ({time})").format(time=now_str)
            else:
                await event.answer(lang.get("inline.auth_rejected_popup", "Login rejected!"))
                title = lang.get("inline.auth_request_title", "TeleSender Web — Login Request")
                done = lang.get("inline.auth_rejected", "Login rejected ({time})").format(time=now_str)
            rejected_text = f"<b>{title}</b>\n\n{done}"
            try:
                await event.edit(rejected_text, buttons=None, parse_mode="html")
            except Exception as e:
                logger.debug("Failed to edit 2FA message: %s", e)

    async def cancel_2fa_request(self, req_id: str) -> bool:
        """Cancel an ongoing 2FA request from web side and update Telegram message."""
        from datetime import datetime
        from src.web.two_factor import two_factor_manager

        req = two_factor_manager.get_request(req_id)
        if not req or req.status != "pending":
            return False

        two_factor_manager.resolve_request(req_id, "cancelled")
        if req.chat_id and req.message_id:
            now_str = datetime.now().strftime("%H:%M:%S")
            purpose = getattr(req, "purpose", "login")
            disable_2fa = purpose == "disable_2fa"
            if disable_2fa:
                title = lang.get("inline.disable_2fa_title", "TeleSender Web — Disable 2FA")
                done = lang.get("inline.disable_2fa_cancelled", "2FA disable cancelled ({time})").format(time=now_str)
            else:
                title = lang.get("inline.auth_request_title", "TeleSender Web — Login Request")
                done = lang.get("inline.auth_cancelled", "Login cancelled by user ({time})").format(time=now_str)
            cancelled_text = f"<b>{title}</b>\n\n{done}"
            try:
                if self.bot_client:
                    await self.bot_client.edit_message(
                        req.chat_id,
                        req.message_id,
                        cancelled_text,
                        buttons=None,
                        parse_mode="html",
                    )
                elif self.bot_api:
                    await self.bot_api.edit_message_text(
                        cancelled_text,
                        chat_id=req.chat_id,
                        message_id=req.message_id,
                        reply_markup={"inline_keyboard": []},
                    )
            except Exception as e:
                logger.debug("Failed to edit 2FA message on cancel: %s", e)
        return True

    def _check_user_access(self, user_id: int, form) -> bool:
        """Verify user permissions for inline form interactions."""
        if getattr(form, "disable_security", False):
            return True

        if user_id in getattr(form, "always_allow", []):
            return True

        if not getattr(form, "force_me", True):
            return True

        if form.owner_id and user_id == form.owner_id:
            return True

        if user_id == self._owner_id:
            return True

        return False
