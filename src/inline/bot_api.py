#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Lightweight HTTP Telegram Bot API client for TeleSender.

    Interacts directly with https://api.telegram.org/bot<token>/ via aiohttp.
    """

import logging
from typing import Any, Dict, List, Optional, Union

import aiohttp

logger = logging.getLogger("telesender.inline.bot_api")


class BotApiClient:
    """Async HTTP wrapper around Telegram Bot API."""

    def __init__(self, token: str):
        self._token = token
        self._base_url = f"https://api.telegram.org/bot{token}"
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        """Close the underlying aiohttp session."""
        if self._session and not self._session.closed:
            await self._session.close()

    async def _post(self, method: str, payload: dict) -> dict:
        """Execute a POST request to Telegram Bot API."""
        session = await self._get_session()
        url = f"{self._base_url}/{method}"
        try:
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                data = await resp.json()
                if not data.get("ok"):
                    logger.debug("Bot API %s error: %s", method, data.get("description"))
                return data
        except Exception as e:
            logger.error("Bot API %s request failed: %s", method, e)
            return {"ok": False, "error": str(e)}

    async def edit_message_text(
        self,
        text: str,
        chat_id: Optional[Union[int, str]] = None,
        message_id: Optional[int] = None,
        inline_message_id: Optional[str] = None,
        reply_markup: Optional[dict] = None,
        parse_mode: str = "HTML",
    ) -> bool:
        """Edit message text via Bot API."""
        payload: Dict[str, Any] = {
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }
        if inline_message_id:
            payload["inline_message_id"] = inline_message_id
        elif chat_id and message_id:
            payload["chat_id"] = chat_id
            payload["message_id"] = message_id
        else:
            return False

        if reply_markup is not None:
            payload["reply_markup"] = reply_markup

        res = await self._post("editMessageText", payload)
        return bool(res.get("ok"))

    async def edit_message_reply_markup(
        self,
        chat_id: Optional[Union[int, str]] = None,
        message_id: Optional[int] = None,
        inline_message_id: Optional[str] = None,
        reply_markup: Optional[dict] = None,
    ) -> bool:
        """Edit only message buttons via Bot API."""
        payload: Dict[str, Any] = {}
        if inline_message_id:
            payload["inline_message_id"] = inline_message_id
        elif chat_id and message_id:
            payload["chat_id"] = chat_id
            payload["message_id"] = message_id
        else:
            return False

        if reply_markup is not None:
            payload["reply_markup"] = reply_markup

        res = await self._post("editMessageReplyMarkup", payload)
        return bool(res.get("ok"))

    async def delete_message(
        self,
        chat_id: Optional[Union[int, str]] = None,
        message_id: Optional[int] = None,
        inline_message_id: Optional[str] = None,
    ) -> bool:
        """Delete message via Bot API (supports both chat/message_id and inline_message_id)."""
        payload: Dict[str, Any] = {}
        if inline_message_id:
            payload["inline_message_id"] = inline_message_id
        elif chat_id and message_id:
            payload["chat_id"] = chat_id
            payload["message_id"] = message_id
        else:
            return False

        res = await self._post("deleteMessage", payload)
        return bool(res.get("ok"))

    async def answer_callback_query(
        self,
        callback_query_id: str,
        text: Optional[str] = None,
        show_alert: bool = False,
    ) -> bool:
        """Answer callback query via Bot API."""
        payload: Dict[str, Any] = {"callback_query_id": str(callback_query_id)}
        if text:
            payload["text"] = text
        if show_alert:
            payload["show_alert"] = True
        res = await self._post("answerCallbackQuery", payload)
        return bool(res.get("ok"))

    async def set_chat_menu_button(self, web_app_url: Optional[str] = None, text: Optional[str] = None) -> bool:
        """Set bot menu button to open a Web App (Mini App) or reset to default."""
        if web_app_url and str(web_app_url).lower().startswith("https://"):
            payload: Dict[str, Any] = {
                "menu_button": {
                    "type": "web_app",
                    "text": text or "Open",
                    "web_app": {"url": web_app_url},
                }
            }
        else:
            payload = {"menu_button": {"type": "default"}}
        res = await self._post("setChatMenuButton", payload)
        return bool(res.get("ok"))
