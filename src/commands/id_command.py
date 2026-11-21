#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ID inspection command."""

from src.commands.base_command import BaseCommand
from src.services.translator import lang


class IdCommand(BaseCommand):
    """Replies with user or chat ID information."""

    async def execute(self, event) -> None:
        parts = event.raw_text.strip().split()

        if len(parts) >= 2:
            username = parts[1].lstrip("@").strip()
            if username:
                try:
                    user = await self.client.get_entity(username)
                    await self._respond(event, self._format_user(user.id, user, username))
                    return
                except Exception:
                    not_found_msg = lang.get(
                        "messages.user_not_found", "User @{username} not found"
                    ).replace("{username}", username)
                    await self._respond(event, not_found_msg)
                    return

        # Replying to a message shows the same user info, but for its author.
        reply_author = await self._reply_author(event)
        if reply_author is not None:
            user_id, entity = reply_author
            await self._respond(event, self._format_user(user_id, entity))
            return

        user_id = event.sender_id or (await event.client.get_me()).id
        entity = None
        try:
            entity = await event.get_chat()
        except Exception:
            pass
        await self._reply_with_chat_info(event, user_id, entity)

    async def _reply_author(self, event):
        """Returns (user_id, entity) for the author of the replied message."""
        try:
            reply_msg = await event.get_reply_message()
        except Exception:
            reply_msg = None
        if reply_msg is None:
            return None

        entity = None
        try:
            entity = await reply_msg.get_sender()
        except Exception:
            entity = None

        user_id = getattr(entity, "id", None) or getattr(reply_msg, "sender_id", None)
        if user_id is None:
            return None

        if entity is None:
            try:
                entity = await self.client.get_entity(user_id)
            except Exception:
                entity = None

        return user_id, entity

    def _format_user(self, user_id, entity, username_hint: str = "") -> str:
        """Formats user info the same way as ``.id @username``."""
        username = self._username(entity) or username_hint
        name = self._entity_name(entity)
        handle = f"@{username}" if username else name
        user_label = lang.get("commands.user", "User")
        name_label = lang.get("commands.name", "Name")
        return (
            f"**{user_label}:** {handle}\n"
            f"**ID:** `{user_id}`\n"
            f"**{name_label}:** {name}"
        )

    async def _reply_with_chat_info(self, event, user_id, entity) -> None:
        """Sends the full .id information block for the current chat."""
        chat_id = event.chat_id
        reply = lang.get(
            "commands.id",
            "**Your ID:** `{user_id}`\n"
            "**Chat ID:** `{chat_id}`\n"
            "**API ID:** `{api_id}`\n"
            "**Username:** @{username}\n"
            "**Title:** {chat_title}",
        ).format(
            user_id=user_id,
            chat_id=chat_id if chat_id is not None else "—",
            api_id=self._api_id(chat_id),
            username=self._username(entity),
            chat_title=self._entity_name(entity),
        )
        await self._respond(event, reply)

    @staticmethod
    def _api_id(chat_id) -> str:
        if chat_id is None:
            return "—"
        value = str(chat_id)
        return value[4:] if value.startswith("-100") else value

    @staticmethod
    def _username(entity) -> str:
        username = getattr(entity, "username", None) or ""
        return str(username).lstrip("@")

    @staticmethod
    def _entity_name(entity) -> str:
        if entity is None:
            return lang.get("messages.unknown", "Unknown")
        title = getattr(entity, "title", None)
        if title:
            return title
        first = getattr(entity, "first_name", None) or ""
        last = getattr(entity, "last_name", None) or ""
        name = f"{first} {last}".strip()
        return name or lang.get("messages.private_chat", "Private chat")
