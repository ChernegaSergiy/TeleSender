#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Inline form manager — creates, normalizes and manages inline forms."""

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional, Union

from telethon import Button, TelegramClient

from .types import ButtonDefinition, InlineForm, generate_form_id

logger = logging.getLogger("telesender.inline.form")


class FormManager:
    """Creates and manages the lifecycle of inline forms."""

    def __init__(self, bot_client: TelegramClient):
        self._bot_client = bot_client
        self._forms: Dict[str, InlineForm] = {}
        self._cleanup_task: Optional[asyncio.Task] = None

    @staticmethod
    def normalize_markup(markup: Any) -> List[List[dict]]:
        """Normalize reply_markup into a 2D list of button dicts."""
        if not markup:
            return []
        if isinstance(markup, dict):
            return [[markup]]
        if isinstance(markup, list):
            if not markup:
                return []
            if isinstance(markup[0], dict):
                # 1D list of dicts -> single row
                return [markup]
            if isinstance(markup[0], list):
                # Already 2D list
                return markup
        return []

    def register_form(
        self,
        text: str,
        chat_id: Union[int, str],
        reply_markup: Any,
        owner_id: int = 0,
        force_me: bool = True,
        ttl: Optional[float] = None,
        disable_security: bool = False,
        always_allow: Optional[List[int]] = None,
    ) -> InlineForm:
        """Register a new inline form in memory and return it."""
        form_id = generate_form_id()

        normalized_markup = self.normalize_markup(reply_markup)
        parsed_buttons: List[List[ButtonDefinition]] = []
        for row in normalized_markup:
            parsed_row = [ButtonDefinition.from_dict(btn) for btn in row]
            parsed_buttons.append(parsed_row)

        form = InlineForm(
            form_id=form_id,
            text=text,
            buttons=parsed_buttons,
            chat_id=chat_id,
            owner_id=owner_id,
            force_me=force_me,
            ttl=ttl,
            disable_security=disable_security,
            always_allow=always_allow or [],
        )

        self._forms[form_id] = form
        logger.debug("Registered inline form %s for chat %s", form_id, chat_id)
        return form

    def get_form(self, form_id: str) -> Optional[InlineForm]:
        """Retrieve a form by ID, returning None if expired or missing."""
        form = self._forms.get(form_id)
        if form is None:
            return None
        if form.is_expired:
            self.remove_form(form_id)
            return None
        return form

    def remove_form(self, form_id: str) -> None:
        """Remove a form from registry."""
        self._forms.pop(form_id, None)

    def build_buttons(
        self,
        reply_markup: Optional[Any],
        form: InlineForm,
    ) -> List[List[Any]]:
        """Build Telethon Inline Buttons from definitions."""
        if reply_markup is not None:
            normalized = self.normalize_markup(reply_markup)
            parsed: List[List[ButtonDefinition]] = []
            for row in normalized:
                parsed.append([ButtonDefinition.from_dict(b) for b in row])
            form.buttons = parsed

        rows = []
        for row in form.buttons:
            row_buttons = []
            for btn in row:
                if btn.url:
                    row_buttons.append(Button.url(btn.text, btn.url))
                elif btn.input is not None:
                    # Switch inline query current chat
                    row_buttons.append(
                        Button.switch_inline(btn.text, query=btn.input, same_peer=True)
                    )
                else:
                    callback_data = f"{form.form_id}:{btn.button_id}".encode("utf-8")
                    row_buttons.append(Button.inline(btn.text, data=callback_data))
            rows.append(row_buttons)

        return rows

    def build_bot_api_keyboard(self, form: InlineForm) -> dict:
        """Build Telegram Bot API inline_keyboard JSON structure (for HTTP Bot API)."""
        inline_keyboard = []
        for row in form.buttons:
            row_buttons = []
            for btn in row:
                btn_dict: Dict[str, Any] = {"text": btn.text}
                if btn.url:
                    btn_dict["url"] = btn.url
                elif btn.input is not None:
                    btn_dict["switch_inline_query_current_chat"] = btn.input
                else:
                    btn_dict["callback_data"] = f"{form.form_id}:{btn.button_id}"
                row_buttons.append(btn_dict)
            inline_keyboard.append(row_buttons)
        return {"inline_keyboard": inline_keyboard}

    def start_cleanup_loop(self, interval: float = 60.0) -> None:
        """Start a background task that removes expired forms."""
        if self._cleanup_task is not None:
            return
        self._cleanup_task = asyncio.create_task(self._cleanup_loop(interval))

    async def _cleanup_loop(self, interval: float) -> None:
        """Periodically purge expired forms."""
        while True:
            try:
                await asyncio.sleep(interval)
                now = time.time()
                expired = [
                    fid
                    for fid, form in self._forms.items()
                    if form.ttl and (now - form.created_at) > form.ttl
                ]
                for fid in expired:
                    form = self._forms.pop(fid, None)
                    if form and form.message_id:
                        try:
                            await self._bot_client.delete_messages(
                                form.chat_id, form.message_id
                            )
                        except Exception:
                            pass
                    logger.debug("Cleaned up expired inline form %s", fid)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in form cleanup loop: %s", e)

    async def stop(self) -> None:
        """Cancel cleanup loop."""
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None
