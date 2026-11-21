#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Inline system type definitions for TeleSender."""

import logging
import secrets
import string
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Union

from telethon import Button
from telethon.events import CallbackQuery

logger = logging.getLogger("telesender.inline.types")


def generate_form_id() -> str:
    """Generate a unique 12-character form identifier."""
    chars = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(chars) for _ in range(12))


def generate_button_id() -> str:
    """Generate a unique button token."""
    chars = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(chars) for _ in range(6))


@dataclass
class ButtonDefinition:
    """Parsed representation of a single inline button."""

    text: str
    callback: Optional[Callable] = None
    args: Optional[list] = None
    kwargs: Optional[dict] = None
    url: Optional[str] = None
    action: Optional[str] = None  # "close", "answer", "unload"
    message: Optional[str] = None  # Text for action: "answer"
    show_alert: bool = False  # Modal alert vs toast for action: "answer"
    input: Optional[str] = None  # Switch inline query current chat
    button_id: str = field(default_factory=generate_button_id)

    @classmethod
    def from_dict(cls, data: dict) -> "ButtonDefinition":
        """Create ButtonDefinition from a dictionary."""
        return cls(
            text=str(data.get("text", "Button")),
            callback=data.get("callback"),
            args=data.get("args") or [],
            kwargs=data.get("kwargs") or {},
            url=data.get("url"),
            action=data.get("action"),
            message=data.get("message") or data.get("text"),
            show_alert=data.get("show_alert", False),
            input=data.get("input"),
            button_id=data.get("button_id") or generate_button_id(),
        )


@dataclass
class InlineForm:
    """Represents an active inline form with buttons."""

    form_id: str
    text: str
    buttons: List[List[ButtonDefinition]]
    chat_id: Union[int, str]
    message_id: Optional[int] = None
    inline_message_id: Optional[str] = None
    owner_id: int = 0
    force_me: bool = True
    ttl: Optional[float] = None
    created_at: float = field(default_factory=time.time)
    disable_security: bool = False
    always_allow: List[int] = field(default_factory=list)

    @property
    def is_expired(self) -> bool:
        """Check whether this form has exceeded its TTL."""
        if self.ttl is None:
            return False
        return (time.time() - self.created_at) > self.ttl

    def find_button(self, button_id: str) -> Optional[ButtonDefinition]:
        """Find a button by its unique ID."""
        for row in self.buttons:
            for btn in row:
                if btn.button_id == button_id:
                    return btn
        return None


class InlineCall:
    """Wrapper around Telethon CallbackQuery event providing convenience helpers.

    Plugins receive this object in their button callback handlers.

    Attributes:
        event: The raw Telethon CallbackQuery.Event.
        form: The InlineForm this call belongs to.
        button: The ButtonDefinition that was pressed.
        form_id: Unique form ID.
        sender_id: Telegram user ID of who pressed the button.
    """

    def __init__(
        self,
        event: CallbackQuery.Event,
        form: InlineForm,
        button: ButtonDefinition,
        manager: Any,
    ):
        self.event = event
        self.form = form
        self.button = button
        self._manager = manager
        self.form_id = form.form_id
        self.sender_id = event.sender_id
        self.data = event.data.decode("utf-8", errors="ignore") if event.data else ""

    async def edit(
        self,
        text: Optional[str] = None,
        reply_markup: Optional[Any] = None,
    ) -> None:
        """Edit the form message text and/or replace the buttons in-place."""
        new_text = text if text is not None else self.form.text
        if text is not None:
            self.form.text = text

        if reply_markup is not None:
            buttons = self._manager.form_manager.build_buttons(reply_markup, self.form)
        else:
            buttons = self._manager.form_manager.build_buttons(None, self.form)

        # Try Bot API first
        bot_api = getattr(self._manager, "bot_api", None)
        edited_via_api = False
        if bot_api and self.form.inline_message_id:
            try:
                json_markup = self._manager.form_manager.build_bot_api_keyboard(self.form)
                edited_via_api = await bot_api.edit_message_text(
                    text=new_text,
                    inline_message_id=self.form.inline_message_id,
                    reply_markup=json_markup,
                )
            except Exception as e:
                logger.debug("Failed to edit via Bot API: %s", e)
                edited_via_api = False

        if not edited_via_api:
            try:
                await self.event.edit(
                    new_text,
                    buttons=buttons,
                    parse_mode="html",
                )
            except Exception as e:
                logger.debug("Failed to edit via event.edit (html): %s, retrying plain", e)
                try:
                    await self.event.edit(new_text, buttons=buttons)
                except Exception as e2:
                    logger.warning("Failed to edit inline message: %s", e2)

    async def delete(self) -> None:
        """Delete the form message and unload the form from memory."""
        bot_api = getattr(self._manager, "bot_api", None)
        deleted = False
        if bot_api and self.form.inline_message_id:
            try:
                deleted = await bot_api.delete_message(
                    inline_message_id=self.form.inline_message_id
                )
            except Exception:
                deleted = False
        if not deleted and bot_api and self.form.chat_id and self.form.message_id:
            try:
                deleted = await bot_api.delete_message(self.form.chat_id, self.form.message_id)
            except Exception:
                deleted = False

        if not deleted:
            try:
                await self.event.delete()
            except Exception:
                pass
        self._manager.form_manager.remove_form(self.form_id)

    async def answer(
        self,
        text: Optional[str] = None,
        show_alert: bool = False,
    ) -> None:
        """Acknowledge the callback query with an optional popup alert or toast."""
        await self.event.answer(message=text or "", alert=show_alert)

    async def unload(self) -> None:
        """Remove the form from internal registry without deleting the message."""
        self._manager.form_manager.remove_form(self.form_id)
