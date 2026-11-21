#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Automated Telegram bot creation and token obtainment via @BotFather.

Configures inline mode automatically, auto-deletes conversation messages
with @BotFather, and persists BOT_TOKEN exclusively in the .env file.
"""

import asyncio
import logging
import os
import random
import re
import string
from typing import Optional
from urllib.parse import urlparse

from telethon import TelegramClient
from telethon.tl.functions.contacts import UnblockRequest

from src.config.config import config
from src.services.translator import lang

logger = logging.getLogger("telesender.inline.token")

_TOKEN_RE = re.compile(r"\d{9,12}:[a-zA-Z0-9_-]{35}")
_BOTFATHER = "@BotFather"
# Bot username template: telesender_{6_chars}_bot
_BOT_USERNAME_PATTERN = re.compile(r"^@?telesender_[0-9a-zA-Z]{6}_bot$", re.IGNORECASE)


class TokenObtainer:
    """Handles automated bot creation and inline mode setup via @BotFather.

    Persists the obtained bot token directly to the .env file.
    """

    def __init__(self, client: TelegramClient, env_path: str = ".env"):
        self._client = client
        self._env_path = env_path

    async def obtain_token(self) -> Optional[str]:
        """Obtain a bot token — from .env or by searching/creating via @BotFather."""
        # 1. Load from .env / runtime config
        saved = self._load_token_from_env()
        if saved:
            logger.info("Loaded bot token from .env")
            return saved

        # 2. Check existing bots in @BotFather or create a new one
        logger.info("No bot token found in .env — querying @BotFather")
        token = await self._assert_botfather_token()
        if token:
            self._save_token_to_env(token)
            return token

        logger.error("Failed to obtain bot token via @BotFather")
        return None

    @staticmethod
    def _extract_login_widget_domain(url: str) -> str:
        """Extract domain/host for BotFather /setdomain (Telegram Login Widget)."""
        raw = (url or "").strip()
        if not raw:
            return ""
        if "://" not in raw:
            raw = "http://" + raw
        try:
            parsed = urlparse(raw)
            host = (parsed.hostname or "").strip().lower()
            if host in ("", "0.0.0.0", "::", "localhost", "127.0.0.1"):
                return ""
            # Telegram Login Widget domain must be a domain name; bare IPs are not
            # suitable for widget domain binding in most cases, but pass through
            # valid hostnames.
            return host
        except Exception:
            return ""

    async def ensure_login_widget_domain(self, bot_username: Optional[str] = None) -> bool:
        """Set Telegram Login Widget domain in BotFather on every restart.

        Uses the user client conversation with @BotFather (/setdomain). Returns
        True if domain was sent successfully, False otherwise.
        """
        bot_username = (bot_username or getattr(config, "bot_username", "") or "").strip()
        if not bot_username.startswith("@"):
            bot_username = f"@{bot_username}" if bot_username else ""
        domain = self._extract_login_widget_domain(getattr(config, "web_url", "") or "")
        if not bot_username or not domain:
            logger.info("Skipping Telegram Login Widget domain setup (bot=%s, domain=%s)", bot_username or "-", domain or "-")
            return False

        try:
            try:
                await self._client(UnblockRequest(id=_BOTFATHER))
            except Exception:
                pass

            async with self._client.conversation(_BOTFATHER, timeout=30, exclusive=False) as conv:
                m1 = await conv.send_message("/setdomain")
                r1 = await conv.get_response(timeout=30)
                await self._safe_delete(m1, r1)

                m2 = await conv.send_message(bot_username)
                r2 = await conv.get_response(timeout=30)
                raw_r2 = (getattr(r2, "raw_text", "") or "").lower()
                await self._safe_delete(m2, r2)

                # If bot selection failed, abort flow cleanly.
                if "choose a bot" in raw_r2 or "invalid bot" in raw_r2:
                    try:
                        mc = await conv.send_message("/cancel")
                        rc = await conv.get_response(timeout=10)
                        await self._safe_delete(mc, rc)
                    except Exception:
                        pass
                    logger.warning("BotFather /setdomain: could not select bot %s", bot_username)
                    return False

                m3 = await conv.send_message(domain)
                r3 = await conv.get_response(timeout=30)
                raw_r3 = getattr(r3, "raw_text", "") or ""
                await self._safe_delete(m3, r3)

                # Best-effort: consider it ok unless BotFather clearly reports an error.
                lowered = raw_r3.lower()
                if "invalid" in lowered or "wrong" in lowered or "sorry" in lowered:
                    logger.warning("BotFather /setdomain response for %s (%s): %s", bot_username, domain, raw_r3.strip())
                    return False

                logger.info("Telegram Login Widget domain set via BotFather: %s -> %s", bot_username, domain)
                return True
        except Exception as e:
            logger.warning("Failed to set Telegram Login Widget domain via BotFather: %s", e)
            return False

    @staticmethod
    def _asset_path(filename: str) -> str:
        """Return path to asset file (cwd/assets first, then project root/assets)."""
        path = os.path.join(os.getcwd(), "assets", filename)
        if os.path.exists(path):
            return path
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        return os.path.join(project_root, "assets", filename)

    @staticmethod
    def _miniapp_loading_icon_path() -> str:
        """Return path to Mini App splash/loading icon asset (SVG, sent as file)."""
        return TokenObtainer._asset_path("telesender_miniapp_loading.svg")

    @staticmethod
    def _miniapp_photo_path() -> str:
        """Return path to Mini App photo asset (640x360 PNG, sent as photo)."""
        return TokenObtainer._asset_path("telesender_640x360.png")

    async def ensure_mini_app_settings(self, bot_username: Optional[str] = None) -> bool:
        """Best-effort: configure Mini App menu button via BotFather /setmenubutton.

        Observed flow: /setmenubutton -> choose bot -> send URL -> send title (or /empty).
        """
        bot_username = (bot_username or getattr(config, "bot_username", "") or "").strip()
        if not bot_username.startswith("@"):
            bot_username = f"@{bot_username}" if bot_username else ""
        web_url = (getattr(config, "web_url", "") or "").rstrip("/")
        if not bot_username or not web_url.lower().startswith("https://"):
            logger.info("Skipping Mini App menu button setup (bot=%s, web_url=%s)", bot_username or "-", web_url or "-")
            return False

        menu_text = (lang.get("inline.open_web", "Open web interface") or "Open web interface")[:64]

        try:
            try:
                await self._client(UnblockRequest(id=_BOTFATHER))
            except Exception:
                pass

            async with self._client.conversation(_BOTFATHER, timeout=40, exclusive=False) as conv:
                m1 = await conv.send_message("/setmenubutton")
                r1 = await conv.get_response(timeout=30)
                await self._safe_delete(m1, r1)

                m2 = await conv.send_message(bot_username)
                r2 = await conv.get_response(timeout=30)
                raw_r2 = (getattr(r2, "raw_text", "") or "").lower()
                await self._safe_delete(m2, r2)
                if "choose a bot" in raw_r2 or "invalid bot" in raw_r2:
                    try:
                        mc = await conv.send_message("/cancel")
                        rc = await conv.get_response(timeout=10)
                        await self._safe_delete(mc, rc)
                    except Exception:
                        pass
                    logger.warning("BotFather /setmenubutton: could not select bot %s", bot_username)
                    return False

                # URL step (BotFather asks for URL first)
                m3 = await conv.send_message(web_url)
                r3 = await conv.get_response(timeout=30)
                raw_r3 = (getattr(r3, "raw_text", "") or "").lower()
                await self._safe_delete(m3, r3)

                if "valid url" in raw_r3 or "https is required" in raw_r3 or "invalid" in raw_r3:
                    try:
                        mc = await conv.send_message("/cancel")
                        rc = await conv.get_response(timeout=10)
                        await self._safe_delete(mc, rc)
                    except Exception:
                        pass
                    logger.warning("BotFather /setmenubutton rejected URL for %s: %s", bot_username, raw_r3.strip())
                    return False

                # Title step
                if "title" in raw_r3 or "displayed on the button" in raw_r3 or "menu" in raw_r3:
                    m4 = await conv.send_message(menu_text)
                    r4 = await conv.get_response(timeout=30)
                    raw_r4 = (getattr(r4, "raw_text", "") or "").lower()
                    await self._safe_delete(m4, r4)
                    if "invalid" in raw_r4 or "sorry" in raw_r4 or "wrong" in raw_r4:
                        logger.warning("BotFather /setmenubutton title step warning: %s", raw_r4.strip())

                logger.info("Mini App menu button configured via BotFather for %s (url=%s)", bot_username, web_url)
                return True
        except Exception as e:
            logger.warning("Failed to configure Mini App menu button via BotFather: %s", e)
            return False

    async def ensure_mini_app(self, bot_username: Optional[str] = None) -> bool:
        """Best-effort: create/configure a Direct Link Mini App via BotFather (/newapp).

        Uses assets/telesender_640x360.png as the 640x360 photo (sent as Photo, not File)
        when BotFather requests the web app picture.
        """
        bot_username = (bot_username or getattr(config, "bot_username", "") or "").strip()
        if not bot_username.startswith("@"):
            bot_username = f"@{bot_username}" if bot_username else ""
        web_url = (getattr(config, "web_url", "") or "").rstrip("/")
        if not bot_username or not web_url.lower().startswith("https://"):
            logger.info("Skipping Mini App (/newapp) setup (bot=%s, web_url=%s)", bot_username or "-", web_url or "-")
            return False

        photo_path = self._miniapp_photo_path()
        title = (lang.get("inline.mini_app_title", "TeleSender Web") or "TeleSender Web")[:64]
        description = (lang.get("inline.mini_app_description", "Open TeleSender web interface") or "Open TeleSender web interface")[:256]
        short_name = "web"

        async def _cancel(conv):
            try:
                mc = await conv.send_message("/cancel")
                rc = await conv.get_response(timeout=10)
                await self._safe_delete(mc, rc)
            except Exception:
                pass

        try:
            try:
                await self._client(UnblockRequest(id=_BOTFATHER))
            except Exception:
                pass

            async with self._client.conversation(_BOTFATHER, timeout=60, exclusive=False) as conv:
                m1 = await conv.send_message("/newapp")
                r1 = await conv.get_response(timeout=30)
                await self._safe_delete(m1, r1)

                m2 = await conv.send_message(bot_username)
                r2 = await conv.get_response(timeout=30)
                raw_r2 = (getattr(r2, "raw_text", "") or "")
                low_r2 = raw_r2.lower()
                await self._safe_delete(m2, r2)

                if "already" in low_r2 and "app" in low_r2:
                    logger.info("Mini App may already exist for %s (BotFather: %s)", bot_username, raw_r2.strip())
                    await _cancel(conv)
                    return True
                if "invalid bot" in low_r2 or "choose a bot" in low_r2:
                    logger.warning("BotFather /newapp: could not select bot %s", bot_username)
                    await _cancel(conv)
                    return False

                # Title
                m3 = await conv.send_message(title)
                r3 = await conv.get_response(timeout=30)
                raw_r3 = (getattr(r3, "raw_text", "") or "").lower()
                await self._safe_delete(m3, r3)
                if "invalid" in raw_r3 or "sorry" in raw_r3:
                    await _cancel(conv)
                    return False

                # Description
                m4 = await conv.send_message(description)
                r4 = await conv.get_response(timeout=30)
                raw_r4 = (getattr(r4, "raw_text", "") or "").lower()
                await self._safe_delete(m4, r4)
                if "invalid" in raw_r4 or "sorry" in raw_r4:
                    await _cancel(conv)
                    return False

                # Photo step (640x360) - must be sent as Photo, not as File.
                if "photo" in raw_r4 or "640" in raw_r4 or "upload" in raw_r4 or "picture" in raw_r4 or "image" in raw_r4:
                    if not os.path.exists(photo_path):
                        logger.warning("Mini App 640x360 photo not found: %s", photo_path)
                        await _cancel(conv)
                        return False
                    try:
                        mp = await conv.send_file(photo_path, force_document=False)
                        r5 = await conv.get_response(timeout=30)
                        raw_r5 = (getattr(r5, "raw_text", "") or "")
                        low_r5 = raw_r5.lower()
                        await self._safe_delete(mp, r5)
                        if "send me the picture as a 'photo'" in low_r5 or "please send a photo" in low_r5:
                            logger.warning("BotFather rejected mini app photo (needs Photo): %s", raw_r5.strip())
                            await _cancel(conv)
                            return False
                        raw_r4 = low_r5
                    except Exception as e:
                        logger.warning("Failed to upload Mini App 640x360 photo in /newapp: %s", e)
                        await _cancel(conv)
                        return False

                # Optional demo GIF step - skip
                if "/empty" in raw_r4 or "gif" in raw_r4 or "demo" in raw_r4:
                    me = await conv.send_message("/empty")
                    r6 = await conv.get_response(timeout=30)
                    raw_r4 = (getattr(r6, "raw_text", "") or "").lower()
                    await self._safe_delete(me, r6)

                # Web App URL
                if "web app url" in raw_r4 or "send me the web app url" in raw_r4 or "url" in raw_r4 or "link" in raw_r4 or raw_r4.strip() == "":
                    mu = await conv.send_message(web_url)
                    r7 = await conv.get_response(timeout=30)
                    raw_r7 = (getattr(r7, "raw_text", "") or "")
                    raw_r4 = raw_r7.lower()
                    await self._safe_delete(mu, r7)

                # Short name
                if "short name" in raw_r4 or "address" in raw_r4 or "t.me/" in raw_r4 or "name for your" in raw_r4:
                    ms = await conv.send_message(short_name)
                    r8 = await conv.get_response(timeout=30)
                    raw_r8 = (getattr(r8, "raw_text", "") or "")
                    low_r8 = raw_r8.lower()
                    await self._safe_delete(ms, r8)
                    if "already" in low_r8 or "taken" in low_r8:
                        logger.info("Mini App short name '%s' already taken/exists: %s", short_name, raw_r8.strip())
                    elif "invalid" in low_r8 or "sorry" in low_r8 or "wrong" in low_r8:
                        logger.warning("BotFather /newapp short name rejected: %s", raw_r8.strip())
                        await _cancel(conv)
                        return False
                    else:
                        logger.info("Mini App created/configured via BotFather for %s (short=%s)", bot_username, short_name)

                if any(x in raw_r4 for x in ("invalid", "wrong", "sorry")):
                    await _cancel(conv)
                    return False

                return True
        except Exception as e:
            logger.warning("Failed to create/configure Mini App via BotFather: %s", e)
            return False

    async def ensure_mini_app_splash_screen(self, bot_username: Optional[str] = None) -> bool:
        """Best-effort: configure Main Mini App and splash screen via BotFather /mybots UI flow.

        Navigates: /mybots -> bot -> Bot Settings -> Configure Mini App (enable/set URL if prompted)
        -> Configure Splash Screen, uploads SVG as file, then sends colors (#121212).
        Button labels/click flow may vary; failures are logged and do not raise.
        """
        bot_username = (bot_username or getattr(config, "bot_username", "") or "").strip()
        bot_name = bot_username.lstrip("@")
        if not bot_name:
            logger.info("Skipping Main Mini App setup (no bot username)")
            return False

        web_url = (getattr(config, "web_url", "") or "").rstrip("/")
        icon_path = self._miniapp_loading_icon_path()

        # Defer all deletions until the whole flow finishes. Deleting a message too early
        # removes the inline keyboard we may still need to click on the next step.
        pending_delete = []

        async def _defer_delete(*messages):
            for msg in messages:
                if msg is not None and msg not in pending_delete:
                    pending_delete.append(msg)

        # Persistent marker: create the Main Mini App only once, for the bot that was
        # just created. On later starts we go straight to "Edit Mini App URL".
        mini_app_owner = self._get_main_mini_app_owner()
        main_app_already_created = mini_app_owner == bot_name
        logger.info(
            "BotFather: Main Mini App marker owner=%s current=%s already_created=%s",
            mini_app_owner or "-",
            bot_name,
            main_app_already_created,
        )

        async def _recent_incoming(limit=20):
            """Recent messages from BotFather (skip our own outgoing ones)."""
            try:
                msgs = await self._client.get_messages(_BOTFATHER, limit=limit)
                return [m for m in (msgs or []) if m and not getattr(m, "out", False)]
            except Exception:
                return []

        async def _get_response(conv, timeout=1):
            try:
                return await conv.get_response(timeout=timeout)
            except Exception:
                # Fallback: BotFather may edit a message instead of sending a new one.
                try:
                    await asyncio.sleep(0.1)
                    incoming = await _recent_incoming(5)
                    if incoming:
                        return incoming[0]
                except Exception:
                    pass
                return None

        async def _after_click(conv, timeout=1):
            await asyncio.sleep(0.15)
            return await _get_response(conv, timeout=timeout)

        async def _click_button(msg, texts, exclude=()):
            """Click a button on the freshest message that actually has matching buttons."""
            candidates = []
            if msg:
                candidates.append(msg)
            try:
                fresh = await self._client.get_messages(_BOTFATHER, ids=getattr(msg, "id", 0))
                if fresh:
                    candidates.insert(0, fresh)
            except Exception:
                pass
            candidates.extend(await _recent_incoming(5))

            seen = set()
            ordered = []
            for c in candidates:
                cid = getattr(c, "id", None)
                if cid is not None:
                    if cid in seen:
                        continue
                    seen.add(cid)
                ordered.append(c)

            for candidate in ordered:
                if not candidate:
                    continue
                for text in texts:
                    try:
                        await candidate.click(text=text)
                        return True, candidate
                    except Exception:
                        continue
                try:
                    for row in (getattr(candidate, "buttons", None) or []):
                        for btn in (row or []):
                            label = (getattr(btn, "text", "") or "")
                            low = label.lower()
                            if any(ex.lower() in low for ex in exclude):
                                continue
                            for text in texts:
                                if text.lower() in low:
                                    await btn.click()
                                    return True, candidate
                except Exception:
                    pass
            return False, msg

        async def _refreshed(msg):
            """Re-fetch a message that BotFather may have edited in place."""
            if not msg:
                return None
            try:
                fresh = await self._client.get_messages(_BOTFATHER, ids=msg.id)
                if fresh and (getattr(fresh, "buttons", None) or (getattr(fresh, "raw_text", "") or "")):
                    return fresh
            except Exception:
                pass
            return msg

        async def _after_button_click(conv, clicked_msg, timeout=0.4):
            """Handle both new BotFather replies and edited-in-place messages."""
            await asyncio.sleep(0.1)
            try:
                resp = await conv.get_response(timeout=timeout)
                if resp and (getattr(resp, "buttons", None) or (getattr(resp, "raw_text", "") or "")):
                    return resp
            except Exception:
                pass
            refreshed = await _refreshed(clicked_msg)
            if refreshed and (getattr(refreshed, "buttons", None) or (getattr(refreshed, "raw_text", "") or "")):
                return refreshed
            incoming = await _recent_incoming(3)
            return incoming[0] if incoming else clicked_msg

        def _is_mini_app_url_prompt(msg):
            raw = (getattr(msg, "raw_text", "") or "").lower().replace("’", "'")
            return (
                "send me the mini app url" in raw
                or ("mini app url" in raw and "send" in raw)
                or "use /empty to disable mini app" in raw
            )

        def _is_splash_prompt(msg):
            raw = (getattr(msg, "raw_text", "") or "").lower().replace("’", "'")
            return (
                "set up the splash screen" in raw
                or ("splash screen" in raw and "svg" in raw)
                or "send an svg file" in raw
            )

        async def _wait_for_prompt(clicked_msg, predicate, attempts=6):
            """Find an exact BotFather prompt by scanning chat, without consuming messages.

            conversation.get_response() eats messages from its internal buffer, so using it
            here was the reason the URL/splash prompts were being skipped. get_messages()
            is non-destructive; we scan recent BotFather replies until the prompt appears.
            """
            min_id = getattr(clicked_msg, "id", 0) or 0
            latest = clicked_msg
            for _ in range(attempts):
                incoming = await _recent_incoming(20)
                if incoming:
                    latest = incoming[0]
                for msg in incoming:
                    mid = getattr(msg, "id", 0) or 0
                    if mid >= min_id and predicate(msg):
                        return msg
                await asyncio.sleep(0.15)
            return latest

        async def _click_button_current(msg, texts, exclude=()):
            """Click only on the current/refreshed message - never on stale BotFather buttons."""
            for candidate in (await _refreshed(msg), msg):
                if not candidate:
                    continue
                for text in texts:
                    try:
                        await candidate.click(text=text)
                        return True, candidate
                    except Exception:
                        continue
                try:
                    for row in (getattr(candidate, "buttons", None) or []):
                        for btn in (row or []):
                            label = (getattr(btn, "text", "") or "")
                            low = label.lower()
                            if any(ex.lower() in low for ex in exclude):
                                continue
                            for text in texts:
                                if text.lower() in low:
                                    await btn.click()
                                    return True, candidate
                except Exception:
                    pass
            return False, msg

        async def _is_asking_for_url(msg):
            raw = (getattr(msg, "raw_text", "") or "").lower() if msg else ""
            if not raw:
                return False
            url_prompts = (
                "send me the url",
                "send me a url",
                "send the url",
                "enter the url",
                "send a valid url",
                "web app url",
                "mini app url",
                "send me the web app url",
                "send me the link",
                "send link",
                "please send me a valid url",
                "https is required",
            )
            return any(p in raw for p in url_prompts) or (
                "url" in raw and any(x in raw for x in ("send", "enter", "edit", "change", "new", "please"))
            )

        async def _maybe_send_url(conv, msg):
            if web_url.lower().startswith("https://") and await _is_asking_for_url(msg):
                mu = await conv.send_message(web_url)
                ru = await _after_click(conv)
                await _defer_delete(mu, ru)
                return ru
            return msg

        try:
            try:
                await self._client(UnblockRequest(id=_BOTFATHER))
            except Exception:
                pass

            async with self._client.conversation(_BOTFATHER, timeout=45, exclusive=False) as conv:
                m = await conv.send_message("/mybots")
                msg = await _get_response(conv)
                await _defer_delete(m)

                # Select bot
                clicked, msg = await _click_button(msg, [f"@{bot_name}", bot_name])
                if not clicked:
                    await _defer_delete(msg)
                    logger.warning("BotFather /mybots: could not find bot button for %s", bot_name)
                    try:
                        mc = await conv.send_message("/cancel")
                        rc = await _get_response(conv, timeout=1)
                        await _defer_delete(mc, rc)
                    except Exception:
                        pass
                    return False
                await _defer_delete(msg)
                msg = await _after_button_click(conv, msg)

                # Bot Settings
                clicked, msg = await _click_button(msg, ["Bot Settings"])
                if not clicked:
                    await _defer_delete(msg)
                    logger.warning("BotFather: 'Bot Settings' button not found for %s (msg=%s)", bot_name, (getattr(msg, 'raw_text', '') or '')[:120])
                    return False
                await _defer_delete(msg)
                msg = await _after_button_click(conv, msg)

                # Configure Mini App (Main Mini App)
                clicked, msg = await _click_button(msg, ["Configure Mini App", "Mini App"])
                if not clicked:
                    await _defer_delete(msg)
                    logger.warning("BotFather: 'Configure Mini App' not found for %s (msg=%s)", bot_name, (getattr(msg, 'raw_text', '') or '')[:120])
                    return False
                await _defer_delete(msg)
                msg = await _after_button_click(conv, msg)

                # Configure Main Mini App URL — strictly deterministic, no blind waiting:
                #   first start / fresh bot  -> "Enable Mini App" -> URL -> splash setup
                #   already created (marker) -> "Edit Mini App URL" -> URL -> FINISH
                # If neither button is found, abort BEFORE any splash assets are sent.
                edit_labels = [
                    "Edit Mini App URL",
                    "Edit Web App URL",
                    "Edit URL",
                    "Edit link",
                    "Change URL",
                    "Set Web App URL",
                    "Configure Web App URL",
                ]
                enable_labels = [
                    "Enable Mini App",
                    "Create Mini App",
                    "Create Main Mini App",
                    "Configure Main Mini App",
                    "Add Mini App",
                    "Set up Mini App",
                    "Enable",
                    "Create",
                ]

                path = None
                if main_app_already_created:
                    # App exists -> do NOT try to create it again. Only edit the URL.
                    logger.info("BotFather: Main Mini App already created for %s, using Edit Mini App URL only", bot_name)
                    edit_clicked, msg = await _click_button(msg, edit_labels)
                    if edit_clicked:
                        path = "edit"
                else:
                    # First start: creation button must be on the current screen.
                    setup_clicked, msg = await _click_button_current(msg, enable_labels)
                    if setup_clicked:
                        path = "enable"
                    else:
                        # Marker missing but BotFather shows Edit -> app exists anyway.
                        edit_clicked, msg = await _click_button(msg, edit_labels)
                        if edit_clicked:
                            path = "edit"

                if path is None:
                    btn_labels = []
                    try:
                        for candidate in await _recent_incoming(20):
                            for row in (getattr(candidate, "buttons", None) or []):
                                for btn in (row or []):
                                    btn_labels.append(getattr(btn, "text", ""))
                    except Exception:
                        pass
                    logger.warning("BotFather: neither Edit Mini App URL nor Enable Mini App found (buttons=%s msg=%s)", btn_labels, (getattr(msg, 'raw_text', '') or '')[:120] if msg else '')
                    try:
                        mexit = await conv.send_message("/cancel")
                        rexit = await _get_response(conv, timeout=1)
                        await _defer_delete(mexit, rexit)
                    except Exception:
                        pass
                    return False

                await _defer_delete(msg)
                # Consume BotFather's exact URL prompt before sending the link:
                # "Send me the Mini App URL that will be opened by tapping the 'Open App' button."
                msg = await _wait_for_prompt(msg, _is_mini_app_url_prompt)
                await _defer_delete(msg)
                if not _is_mini_app_url_prompt(msg):
                    logger.warning("BotFather: Mini App URL prompt not detected (msg=%s); sending URL anyway", (getattr(msg, 'raw_text', '') or '')[:120])

                if not web_url.lower().startswith("https://"):
                    logger.warning("BotFather: web_url is not HTTPS, cannot set Mini App URL: %s", web_url)
                    return False

                mu = await conv.send_message(web_url)
                msg = await _after_click(conv, timeout=0.4)
                await _defer_delete(mu, msg)
                logger.info("BotFather: %s Mini App URL sent: %s", path, web_url)

                # Existing app: only update the URL and stop here (no splash reconfiguration).
                if path == "edit":
                    self._mark_main_mini_app_created(bot_name)
                    logger.info("BotFather: existing Mini App URL updated, finishing")
                    return True

                # Fresh app: « Back to Bot -> Bot Settings -> Configure Mini App
                # -> Configure Splash Screen. Only after that send SVG + 2 messages.
                reached_splash = False
                for _ in range(5):
                    # 1. "« Back to Bot" (never "Back to Bot List"). No need to type « manually.
                    clicked, msg = await _click_button(
                        msg,
                        ["« Back to Bot", "Back to Bot"],
                        exclude=["Back to Bot List"],
                    )
                    if not clicked:
                        clicked, msg = await _click_button(
                            msg,
                            ["Back to Settings", "« Back", "Back"],
                            exclude=["Back to Bot List"],
                        )
                    if clicked:
                        await _defer_delete(msg)
                        msg = await _after_button_click(conv, msg, timeout=0.4)

                    # 2. Bot Settings
                    clicked, msg = await _click_button(msg, ["Bot Settings"])
                    if clicked:
                        await _defer_delete(msg)
                        msg = await _after_button_click(conv, msg, timeout=0.4)

                    # 3. Configure Mini App
                    clicked, msg = await _click_button(msg, ["Configure Mini App", "Mini App"])
                    if clicked:
                        await _defer_delete(msg)
                        msg = await _after_button_click(conv, msg, timeout=0.4)

                    # 4. Configure Splash Screen
                    clicked, msg = await _click_button(msg, ["Configure Splash Screen", "Splash Screen"])
                    if clicked:
                        await _defer_delete(msg)
                        # Consume BotFather's exact splash prompt before uploading the SVG:
                        # "Let's set up the splash screen. First, send an SVG file ..."
                        msg = await _wait_for_prompt(msg, _is_splash_prompt)
                        await _defer_delete(msg)
                        if not _is_splash_prompt(msg):
                            logger.warning("BotFather: splash prompt not detected (msg=%s); uploading icon anyway", (getattr(msg, 'raw_text', '') or '')[:120])
                        reached_splash = True
                        break

                if not reached_splash:
                    btn_labels = []
                    try:
                        for candidate in await _recent_incoming(20):
                            for row in (getattr(candidate, "buttons", None) or []):
                                for btn in (row or []):
                                    btn_labels.append(getattr(btn, "text", ""))
                    except Exception:
                        pass
                    logger.warning("BotFather: could not reach Configure Splash Screen (buttons=%s msg=%s)", btn_labels, (getattr(msg, 'raw_text', '') or '')[:120] if msg else '')
                    try:
                        mexit = await conv.send_message("/cancel")
                        rexit = await _get_response(conv, timeout=1)
                        await _defer_delete(mexit, rexit)
                    except Exception:
                        pass
                    return False

                # Upload SVG icon as file for splash screen
                if icon_path and os.path.exists(icon_path):
                    mfile = await conv.send_file(icon_path, force_document=True)
                    rfile = await _after_click(conv)
                    await _defer_delete(mfile, rfile)
                else:
                    await _defer_delete(msg)
                    logger.warning("Mini App splash icon not found: %s", icon_path)
                    return False

                # Send color values as requested (#121212 #121212, then #121212 #121212)
                for pair in ("#121212 #121212", "#121212 #121212"):
                    mc = await conv.send_message(pair)
                    rc = await _after_click(conv)
                    await _defer_delete(mc, rc)

                try:
                    mexit = await conv.send_message("/cancel")
                    rexit = await _get_response(conv, timeout=1)
                    await _defer_delete(mexit, rexit)
                except Exception:
                    pass

                self._mark_main_mini_app_created(bot_name)
                logger.info("Main Mini App/splash screen configured via BotFather for %s", bot_name)
                return True
        except Exception as e:
            logger.warning("Failed to configure Main Mini App/splash screen via BotFather: %s", e)
            return False
        finally:
            # Only clean up BotFather messages after the whole setup is done.
            if pending_delete:
                await self._safe_delete(*pending_delete)

    async def _safe_delete(self, *messages) -> None:
        """Safely delete messages from @BotFather chat."""
        ids = [getattr(msg, "id", None) for msg in messages if msg is not None]
        ids = [mid for mid in ids if mid is not None]
        if ids:
            try:
                await self._client.delete_messages(_BOTFATHER, ids)
                return
            except Exception:
                pass
        for msg in messages:
            if msg:
                try:
                    await msg.delete()
                except Exception:
                    pass

    async def _assert_botfather_token(self) -> Optional[str]:
        """Search @BotFather for an existing telesender bot, or create a new one."""
        try:
            # Unblock BotFather if blocked
            try:
                await self._client(UnblockRequest(id=_BOTFATHER))
            except Exception:
                pass

            async with self._client.conversation(_BOTFATHER, timeout=30, exclusive=False) as conv:
                # Ask BotFather for current bots
                m1 = await conv.send_message("/token")
                r1 = await conv.get_response(timeout=30)

                bot_username = None

                # Look for bot matching telesender_{6_chars}_bot template
                if hasattr(r1, "reply_markup") and hasattr(r1.reply_markup, "rows"):
                    for row in r1.reply_markup.rows:
                        for button in row.buttons:
                            btn_text = getattr(button, "text", "").strip()
                            if _BOT_USERNAME_PATTERN.search(btn_text):
                                bot_username = btn_text
                                break
                        if bot_username:
                            break

                # Delete /token prompt and response
                await self._safe_delete(m1, r1)

                if bot_username:
                    logger.info("Found bot %s in @BotFather, retrieving token...", bot_username)
                    m2 = await conv.send_message(bot_username)
                    r2 = await conv.get_response(timeout=30)
                    raw_text = r2.raw_text
                    await self._safe_delete(m2, r2)

                    token = self._extract_token(raw_text)
                    if token:
                        logger.info("Retrieved token for bot %s", bot_username)
                        self._save_bot_username_to_env(bot_username)
                        await self._configure_bot(conv, bot_username)
                        return token

                # If no matching bot exists, create a new one
                return await self._create_new_bot(conv)

        except Exception as e:
            logger.error("Error communicating with @BotFather: %s", e, exc_info=True)
            return None

    async def _create_new_bot(self, conv, max_retries: int = 5) -> Optional[str]:
        """Create a new bot with telesender_{random}_bot username and auto-delete messages."""
        # Resolve user display name ("TeleSender Userbot of <name>")
        user_name = "User"
        try:
            me = await self._client.get_me()
            parts = []
            if getattr(me, "first_name", None):
                parts.append(me.first_name)
            if getattr(me, "last_name", None):
                parts.append(me.last_name)
            user_name = " ".join(parts) if parts else (
                getattr(me, "username", None) or "User"
            )
        except Exception:
            pass

        for attempt in range(max_retries):
            try:
                uid = self._generate_random_suffix(6)
                username = f"telesender_{uid}_bot"
                display_name = lang.get("inline.bot_display_name", "TeleSender Userbot of {name}").format(name=user_name)[:64]

                logger.info(
                    "Attempt %d/%d — creating bot @%s via @BotFather",
                    attempt + 1,
                    max_retries,
                    username,
                )

                # /newbot
                m1 = await conv.send_message("/newbot")
                r1 = await conv.get_response(timeout=30)
                raw_r1 = r1.raw_text
                await self._safe_delete(m1, r1)

                if "20" in raw_r1:
                    logger.warning("Bot limit reached in @BotFather")
                    return None

                await asyncio.sleep(0.5)

                # Display name
                m2 = await conv.send_message(display_name)
                r2 = await conv.get_response(timeout=30)
                await self._safe_delete(m2, r2)

                await asyncio.sleep(0.5)

                # Username
                m3 = await conv.send_message(f"@{username}")
                r3 = await conv.get_response(timeout=30)
                raw_r3 = r3.raw_text
                await self._safe_delete(m3, r3)

                if "already taken" in raw_r3.lower():
                    logger.warning("Username @%s already taken, retrying...", username)
                    # Cancel current BotFather flow before retrying to avoid state confusion
                    try:
                        mc = await conv.send_message("/cancel")
                        rc = await conv.get_response(timeout=10)
                        await self._safe_delete(mc, rc)
                    except Exception:
                        pass
                    continue

                token = self._extract_token(raw_r3)
                if token:
                    logger.info("Created bot @%s successfully", username)
                    self._save_bot_username_to_env(username)
                    await self._configure_bot(conv, f"@{username}")
                    return token

            except Exception as e:
                logger.error("Error creating bot on attempt %d: %s", attempt + 1, e)
                await asyncio.sleep(1.0)

        return None

    async def _configure_bot(self, conv, bot_username: str) -> None:
        """Enable inline mode, feedback, and set avatar from assets/telesender_bot_avatar.png."""
        # 1. Inline mode and feedback
        for msg in [
            "/setinline",
            bot_username,
            "telesender:~$",
            "/setinlinefeedback",
            bot_username,
            "Enabled",
        ]:
            try:
                m = await conv.send_message(msg)
                r = await conv.get_response()
                await self._safe_delete(m, r)
                await asyncio.sleep(0.3)
            except Exception as e:
                logger.debug("Inline mode setup step '%s' warning: %s", msg, e)

        # 2. Bot avatar from assets/telesender_bot_avatar.png
        avatar_path = os.path.join(os.getcwd(), "assets", "telesender_bot_avatar.png")
        if not os.path.exists(avatar_path):
            project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
            avatar_path = os.path.join(project_root, "assets", "telesender_bot_avatar.png")

        if os.path.exists(avatar_path):
            try:
                m1 = await conv.send_message("/setuserpic")
                r1 = await conv.get_response()
                await self._safe_delete(m1, r1)

                m2 = await conv.send_message(bot_username)
                r2 = await conv.get_response()
                await self._safe_delete(m2, r2)

                m3 = await conv.send_file(avatar_path, force_document=False)
                r3 = await conv.get_response()
                await self._safe_delete(m3, r3)
                logger.info("Bot avatar set successfully from %s", avatar_path)
            except Exception as e:
                logger.warning("Failed to set bot avatar: %s", e)
                try:
                    mc = await conv.send_message("/cancel")
                    rc = await conv.get_response()
                    await self._safe_delete(mc, rc)
                except Exception:
                    pass

        logger.info("Bot %s fully configured", bot_username)

    @staticmethod
    def _generate_random_suffix(length: int = 6) -> str:
        chars = string.ascii_lowercase + string.digits
        return "".join(random.choice(chars) for _ in range(length))

    @staticmethod
    def _extract_token(text: str) -> Optional[str]:
        match = _TOKEN_RE.search(text)
        return match.group(0) if match else None

    def _load_token_from_env(self) -> Optional[str]:
        """Read token from memory config or directly from .env file."""
        if getattr(config, "bot_token", None):
            return config.bot_token

        token_from_os = os.getenv("BOT_TOKEN") or os.getenv("INLINE_BOT_TOKEN")
        if token_from_os and token_from_os.strip():
            return token_from_os.strip()

        if os.path.exists(self._env_path):
            try:
                with open(self._env_path, "r", encoding="utf-8") as f:
                    content = f.read()
                m = re.search(r"^\s*(?:BOT_TOKEN|INLINE_BOT_TOKEN)\s*=\s*['\"]?([^'\"\s\r\n]+)", content, re.MULTILINE)
                if m:
                    return m.group(1).strip()
            except Exception as e:
                logger.warning("Could not read %s: %s", self._env_path, e)

        return None

    def _save_bot_username_to_env(self, username: str) -> None:
        """Save BOT_USERNAME to the .env file and update runtime config."""
        username = (username or "").strip().lstrip("@")
        if not username:
            return
        os.environ["BOT_USERNAME"] = username
        config.bot_username = username

        try:
            content = ""
            if os.path.exists(self._env_path):
                with open(self._env_path, "r", encoding="utf-8") as f:
                    content = f.read()

            if re.search(r"^\s*BOT_USERNAME\s*=.*$", content, re.MULTILINE):
                content = re.sub(
                    r"^\s*BOT_USERNAME\s*=.*$",
                    f"BOT_USERNAME={username}",
                    content,
                    flags=re.MULTILINE,
                )
            else:
                if content and not content.endswith("\n"):
                    content += "\n"
                content += f"BOT_USERNAME={username}\n"

            with open(self._env_path, "w", encoding="utf-8") as f:
                f.write(content)

            logger.info("Saved BOT_USERNAME to %s", self._env_path)
        except Exception as e:
            logger.error("Failed to write BOT_USERNAME to %s: %s", self._env_path, e)

    def _get_main_mini_app_owner(self) -> Optional[str]:
        """Return the bot username for which the Main Mini App was already created."""
        value = (os.getenv("MAIN_MINI_APP_CREATED", "") or "").strip().lstrip("@")
        if not value and os.path.exists(self._env_path):
            try:
                with open(self._env_path, "r", encoding="utf-8") as f:
                    content = f.read()
                m = re.search(
                    r"^\s*MAIN_MINI_APP_CREATED\s*=\s*['\"]?([^'\"\s\r\n]+)",
                    content,
                    re.MULTILINE,
                )
                if m:
                    value = m.group(1).strip().lstrip("@")
            except Exception as e:
                logger.warning("Could not read MAIN_MINI_APP_CREATED from %s: %s", self._env_path, e)
        return value or None

    def _mark_main_mini_app_created(self, bot_name: str) -> None:
        """Persist that the Main Mini App exists for this bot (no re-creation on restart)."""
        bot_name = (bot_name or "").strip().lstrip("@")
        if not bot_name:
            return
        os.environ["MAIN_MINI_APP_CREATED"] = bot_name
        try:
            content = ""
            if os.path.exists(self._env_path):
                with open(self._env_path, "r", encoding="utf-8") as f:
                    content = f.read()
            if re.search(r"^\s*MAIN_MINI_APP_CREATED\s*=.*$", content, re.MULTILINE):
                content = re.sub(
                    r"^\s*MAIN_MINI_APP_CREATED\s*=.*$",
                    f"MAIN_MINI_APP_CREATED={bot_name}",
                    content,
                    flags=re.MULTILINE,
                )
            else:
                if content and not content.endswith("\n"):
                    content += "\n"
                content += f"MAIN_MINI_APP_CREATED={bot_name}\n"
            with open(self._env_path, "w", encoding="utf-8") as f:
                f.write(content)
            logger.info("Saved MAIN_MINI_APP_CREATED=%s to %s", bot_name, self._env_path)
        except Exception as e:
            logger.error("Failed to write MAIN_MINI_APP_CREATED to %s: %s", self._env_path, e)

    def _save_token_to_env(self, token: str) -> None:
        """Save BOT_TOKEN to the .env file and update runtime environment."""
        token = token.strip()
        os.environ["BOT_TOKEN"] = token
        config.bot_token = token

        try:
            content = ""
            if os.path.exists(self._env_path):
                with open(self._env_path, "r", encoding="utf-8") as f:
                    content = f.read()

            if re.search(r"^\s*BOT_TOKEN\s*=.*$", content, re.MULTILINE):
                # Replace existing line
                content = re.sub(
                    r"^\s*BOT_TOKEN\s*=.*$",
                    f"BOT_TOKEN={token}",
                    content,
                    flags=re.MULTILINE,
                )
            else:
                # Append to end of .env
                if content and not content.endswith("\n"):
                    content += "\n"
                content += f"BOT_TOKEN={token}\n"

            with open(self._env_path, "w", encoding="utf-8") as f:
                f.write(content)

            logger.info("Saved BOT_TOKEN to %s", self._env_path)
        except Exception as e:
            logger.error("Failed to write BOT_TOKEN to %s: %s", self._env_path, e)
