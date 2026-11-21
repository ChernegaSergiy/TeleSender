#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Event handling and routing for inline bot interactions (Telethon-based)."""

import logging
from typing import TYPE_CHECKING, Optional, Any

from telethon import Button, TelegramClient, events
from telethon.events import CallbackQuery, InlineQuery, NewMessage

from src.config.config import config
from src.services.translator import lang
from src.web.sessions import create_bot_login_code
from .types import InlineCall

if TYPE_CHECKING:
    from .manager import InlineManager

logger = logging.getLogger("telesender.inline.events")


def _build_web_login_url(code: str) -> str:
    base = (config.web_url or "").rstrip("/")
    return f"{base}/auth/bot-login?code={code}"


def _build_open_button(text: str, url: str):
    """Build button that opens web interface (Mini App for https, URL for http)."""
    if (url or "").lower().startswith("https://"):
        from telethon.tl import types as tl_types
        return tl_types.KeyboardInlineButton(
            text,
            tl_types.InlineButtonTypeWebView(url),
        )
    return Button.url(text, url)


class EventRouter:
    """Routes CallbackQuery, InlineQuery, and /start messages for the bot."""

    def __init__(self, manager: "InlineManager", bot_client: TelegramClient):
        self._manager = manager
        self._bot_client = bot_client
        self._register_handlers()

    def _register_handlers(self) -> None:
        """Register Telethon event handlers on the bot client."""

        @self._bot_client.on(events.CallbackQuery)
        async def on_callback(event: CallbackQuery.Event) -> None:
            await self._handle_callback(event)

        @self._bot_client.on(events.InlineQuery)
        async def on_inline(event: InlineQuery.Event) -> None:
            await self._handle_inline_query(event)

        @self._bot_client.on(events.NewMessage(pattern=r"^/start"))
        async def on_start(event: NewMessage.Event) -> None:
            await self._handle_start(event)

    # ------------------------------------------------------------------
    # Callback query handling
    # ------------------------------------------------------------------

    async def _handle_callback(self, event: CallbackQuery.Event) -> None:
        """Handle button clicks."""
        try:
            raw_data = event.data.decode("utf-8", errors="ignore") if event.data else ""

            # Check if this is a 2FA callback query
            if raw_data.startswith("2fa:"):
                parts = raw_data.split(":")
                if len(parts) >= 3:
                    await self._manager.handle_2fa_callback(event, parts[1], parts[2])
                return

            # Open web interface button: verify clicker's Telegram ID and issue
            # a one-time login code only for allowed users.
            if raw_data == "web:open":
                sender_id = event.sender_id
                base_url = (config.web_url or "").rstrip("/")
                if sender_id is not None and self._check_inline_access(sender_id) and int(sender_id) in (config.allowed_users or []):
                    code = create_bot_login_code(int(sender_id))
                    login_url = _build_web_login_url(code)
                    try:
                        await event.answer(lang.get("inline.web_login_ready", "Authorized. Tap to open."), alert=False)
                    except Exception:
                        pass
                    try:
                        await event.edit(
                            buttons=[[ _build_open_button(lang.get("inline.open_web", "Open web interface"), login_url) ]]
                        )
                    except Exception:
                        try:
                            await event.edit(
                                buttons=[[Button.url(lang.get("inline.open_web", "Open web interface"), login_url)]]
                            )
                        except Exception:
                            pass
                else:
                    login_url = f"{base_url}/login" if base_url else "/login"
                    try:
                        await event.answer(lang.get("inline.web_login_password", "Open login page."), alert=False)
                    except Exception:
                        pass
                    try:
                        await event.edit(
                            buttons=[[ _build_open_button(lang.get("inline.open_web", "Open web interface"), login_url) ]]
                        )
                    except Exception:
                        try:
                            await event.edit(
                                buttons=[[Button.url(lang.get("inline.open_web", "Open web interface"), login_url)]]
                            )
                        except Exception:
                            pass
                return

            if ":" not in raw_data:
                await event.answer(lang.get("inline.unknown_action", "Unknown action"), alert=True)
                return

            form_id, button_id = raw_data.split(":", 1)
            form = self._manager.form_manager.get_form(form_id)

            if form is None:
                await event.answer(lang.get("inline.form_expired", "This form has expired or was removed"), alert=True)
                return

            # Capture inline_message_id from the callback event if not already set
            inline_message_id = getattr(event, "inline_message_id", None)
            if inline_message_id and not form.inline_message_id:
                form.inline_message_id = inline_message_id
                logger.debug(
                    "Captured inline_message_id %s from callback for form %s",
                    inline_message_id,
                    form.form_id,
                )

            # Security verification
            if not self._check_access(event.sender_id, form):
                await event.answer(lang.get("inline.access_denied", "Access denied"), alert=True)
                return

            button = form.find_button(button_id)
            if button is None:
                await event.answer(lang.get("inline.button_not_found", "Button not found"), alert=True)
                return

            call = InlineCall(
                event=event,
                form=form,
                button=button,
                manager=self._manager,
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
            logger.error("Error handling callback query: %s", e, exc_info=True)
            try:
                await event.answer(lang.get("inline.error_processing", "Error processing request"), alert=True)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Inline query handling
    # ------------------------------------------------------------------

    async def _handle_inline_query(self, event: InlineQuery.Event) -> None:
        """Handle inline queries typed by users or invoked by form creation."""
        try:
            query_text = (event.text or "").strip()
            sender_id = event.sender_id

            # Security: only allow inline queries from authorized users
            if not self._check_inline_access(sender_id):
                await event.answer([], cache_time=5)
                return

            # 1. Check if this is an active inline form invocation
            form = self._manager.form_manager.get_form(query_text)
            if form:
                buttons = self._manager.form_manager.build_buttons(None, form)
                article = event.builder.article(
                    title=lang.get("inline.form_title", "TeleSender Form"),
                    text=form.text,
                    buttons=buttons,
                    parse_mode="html",
                    link_preview=False,
                )
                await event.answer([article], cache_time=0)
                return

            # 2. Otherwise check plugin registered inline handlers
            registry = self._manager.plugin_registry
            if not registry:
                await event.answer([])
                return

            results = []
            inline_handlers = getattr(registry, "_inline_handlers", {})

            for inline_id, handler_info in inline_handlers.items():
                if not getattr(handler_info, "is_active", True):
                    continue
                try:
                    plugin_inst = registry.get_plugin_instance(handler_info.plugin_name)
                    if plugin_inst:
                        res = await handler_info.handler(plugin_inst, event)
                        if isinstance(res, list):
                            results.extend(res)
                except Exception as exc:
                    logger.error("Error in inline handler %s: %s", inline_id, exc)

            if not results:
                # Default friendly fallback
                builder = event.builder
                results = [
                    builder.article(
                        title=lang.get("inline.assistant_title", "TeleSender Assistant"),
                        text=lang.get("inline.bot_active", "<b>TeleSender</b> inline bot is active."),
                        description=lang.get("inline.bot_description", "TeleSender modular userbot"),
                        parse_mode="html",
                    )
                ]

            await event.answer(results[:50], cache_time=5)

        except Exception as e:
            logger.error("Error handling inline query: %s", e)
            try:
                await event.answer([])
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Bot PM handling
    # ------------------------------------------------------------------

    async def _handle_start(self, event: NewMessage.Event) -> None:
        """Reply to /start — only for authorized users."""
        sender_id = event.sender_id

        if not self._check_inline_access(sender_id):
            try:
                await event.reply(
                    lang.get(
                        "inline.bot_start_denied",
                        "<b>Access denied</b>\n\nThis bot is restricted to authorized users only.",
                    ),
                    parse_mode="html",
                )
            except Exception as e:
                logger.debug("Failed to send /start denied reply: %s", e)
            return

        text = lang.get(
            "inline.bot_start_text",
            "<b>TeleSender Inline Bot</b>\n\nThis bot powers interactive inline forms and buttons for TeleSender plugins.\nYou can interact with it via plugin commands.",
        )

        # Open web interface button. Use Mini App (WebView) only in private chats
        # where supported; in groups/channels fall back to a regular URL button to
        # avoid ButtonTypeInvalid/ButtonUrlInvalid errors.
        buttons = None
        web_url = (config.web_url or "").rstrip("/")
        if getattr(config, "web_enabled", False) and web_url:
            open_text = lang.get("inline.open_web", "Open web interface")
            try:
                if event.is_private:
                    buttons = [[_build_open_button(open_text, web_url)]]
                else:
                    buttons = [[Button.url(open_text, web_url)]]
            except Exception:
                buttons = [[Button.url(open_text, web_url)]]

        try:
            await event.reply(text, parse_mode="html", buttons=buttons)
        except Exception as e:
            logger.warning("Failed to reply to /start with buttons (%s), retrying without Mini App button", e)
            # Retry with plain URL button, then without buttons at all.
            try:
                if getattr(config, "web_enabled", False) and web_url:
                    await event.reply(
                        text,
                        parse_mode="html",
                        buttons=[[Button.url(lang.get("inline.open_web", "Open web interface"), web_url)]],
                    )
                else:
                    await event.reply(text, parse_mode="html")
            except Exception as e2:
                logger.warning("Failed to reply to /start with URL button (%s), retrying without buttons", e2)
                try:
                    await event.reply(text, parse_mode="html")
                except Exception as e3:
                    logger.error("Failed to send /start reply: %s", e3, exc_info=True)

    # ------------------------------------------------------------------
    # Security helper
    # ------------------------------------------------------------------

    def _check_access(self, user_id: int, form: Any) -> bool:
        """Verify user permissions for form button interactions."""
        if getattr(form, "disable_security", False):
            return True

        # Check always_allow list
        if user_id in getattr(form, "always_allow", []):
            return True

        # If force_me is disabled, anyone can interact
        if not getattr(form, "force_me", True):
            return True

        # Check owner / sender
        if form.owner_id and user_id == form.owner_id:
            return True

        # Check TeleSender authorized owner / allowed users
        if user_id == self._manager._owner_id:
            return True

        return False

    def _check_inline_access(self, user_id: int) -> bool:
        """Verify user is authorized to use the inline bot (inline queries, /start)."""
        # Allow the owner
        if user_id == self._manager._owner_id:
            return True

        # Check against configured allowed users
        allowed = getattr(self._manager, "_allowed_users", None)
        if allowed is None:
            # Fall back to config if available
            try:
                from src.config.config import config
                allowed = config.allowed_users
            except Exception:
                allowed = []

        if allowed and user_id in allowed:
            return True

        return False
