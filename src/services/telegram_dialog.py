#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Interactive Telegram dialogs for /add and /edit commands.

Uses a state machine driven by message events. Each step asks a question
and waits for the user's reply (text or media upload). On completion the
task is created/updated via DatabaseService/TaskService.
"""

import os
import asyncio
import re
import tempfile
from typing import Any, Dict, List, Optional

from telethon import Button
from telethon.events import NewMessage
from telethon.tl.types import (
    MessageEntityBold,
    MessageEntityItalic,
    MessageEntityCode,
    MessageEntityPre,
    MessageEntityTextUrl,
    MessageEntityUrl,
    MessageEntityMention,
    MessageEntityHashtag,
    MessageEntityCashtag,
    MessageEntityBotCommand,
    MessageEntityEmail,
    MessageEntityPhone,
    MessageEntityUnderline,
    MessageEntityStrike,
    MessageEntityBlockquote,
    MessageEntitySpoiler,
    MessageEntityCustomEmoji,
)

from src.services.database_service import Colors, print_color
from src.services.gallery_service import gallery_service
from src.services.logger import log
from src.services.translator import lang


MEDIA_TYPE_MAP = {
    "photo": "photo", "фото": "photo", "image": "photo", "p": "photo",
    "video": "video", "відео": "video", "видео": "video", "v": "video",
    "document": "document", "doc": "document", "документ": "document",
    "д": "document", "file": "document", "файл": "document",
    "audio": "audio", "аудіо": "audio", "аудио": "audio", "a": "audio", "au": "audio",
    "sticker": "sticker", "стікер": "sticker", "стикер": "sticker", "s": "sticker", "st": "sticker",
    "gif": "gif", "гіф": "gif", "g": "gif",
    "voice": "voice", "голосове": "voice", "голосовое": "voice",
    "video_note": "video_note", "vn": "video_note", "кружок": "video_note",
}


def _normalize_media_type(raw: str) -> Optional[str]:
    if not raw:
        return None
    return MEDIA_TYPE_MAP.get(raw.strip().lower())


def _media_label(media_type: Optional[str]) -> str:
    keys = {
        "photo": "media.photo", "video": "media.video", "document": "media.document",
        "audio": "media.audio", "sticker": "media.sticker", "gif": "media.gif",
        "voice": "media.voice", "video_note": "media.video_note",
    }
    if not media_type:
        return lang.get("messages.media_file", "file")
    return lang.get(keys.get(media_type, ""), media_type)


def _detect_media_type_from_file(path: str) -> Optional[str]:
    ext = os.path.splitext(path)[1].lower()
    if ext in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"):
        return "sticker" if ext == ".webp" else "photo"
    if ext in (".mp4", ".mov", ".avi", ".mkv", ".webm"):
        return "video"
    if ext in (".mp3", ".ogg", ".m4a", ".wav"):
        return "audio"
    if ext in (".pdf", ".doc", ".docx", ".txt", ".zip", ".rar"):
        return "document"
    return None


def _gallery_category(media_type: str) -> str:
    return {
        "photo": "image", "video": "video", "document": "document",
        "audio": "other", "sticker": "image", "gif": "image",
        "voice": "other", "video_note": "video",
    }.get(media_type, "other")


class TelegramDialog:
    """Stateful step-by-step Telegram dialog for creating/editing tasks."""

    def __init__(self, bot, event, mode: str = "add", task_id: Optional[int] = None):
        self.bot = bot
        self.event = event
        self.mode = mode
        self.task_id = task_id
        self.chat = event.chat_id

        self.db = bot.db
        self.task_service = getattr(bot, "task_service", None)
        self.scheduler = getattr(bot, "scheduler_service", None)
        self.telegram_service = getattr(bot, "telegram_service", None)
        self.client = getattr(bot, "client", None)

        self.state: Dict[str, Any] = {
            "chats": [],
            "message": "",
            "media_path": None,
            "media_type": None,
            "interval": None,
            "is_once": 0,
            "once_time": None,
            "messages_list": [],
            "step": "chats",
            "rotation_mode": False,
            "awaiting_upload": False,
        }

        self._message_id: Optional[int] = None
        self._handler = None
        self._running = False

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------
    async def start(self):
        self._running = True
        self._register_handler()
        if self.mode == "edit":
            await self._start_edit()
        else:
            await self._step_chats()

    async def stop(self):
        self._running = False
        if self._handler is not None:
            try:
                self.client.remove_event_handler(self._dispatch, NewMessage)
            except Exception:
                pass
            self._handler = None

    def _register_handler(self):
        try:
            self._handler = self._dispatch
            self.client.add_event_handler(self._dispatch, NewMessage)
        except Exception as e:
            log.error(f"TelegramDialog handler register error: {e}")

    async def _dispatch(self, event):
        if not self._running:
            return
        if event.chat_id != self.chat:
            return
        if getattr(event, "sender_id", None) != getattr(self.event, "sender_id", None):
            return
        text = getattr(event, "raw_text", "") or ""
        # Let real Telegram commands (.add, .edit, .tasks, ...) pass through to
        # the CommandHandler instead of being swallowed by the dialog.
        if text.startswith(".") and not text.strip().lower().startswith((".cancel", ".done", ">>")):
            return
        try:
            await self._on_message(event)
        except Exception as e:
            log.exception(f"TelegramDialog dispatch error: {e}")
        finally:
            # Auto-delete the user's reply so the chat stays clean.
            try:
                reply_id = getattr(event, "id", None)
                if reply_id is not None:
                    await self.event.client.delete_messages(self.chat, [reply_id])
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _t(self, key: str, default: str, **kwargs) -> str:
        text = lang.get(key, default)
        for k, v in kwargs.items():
            text = text.replace("{" + k + "}", str(v))
        return text

    async def _reply(self, text: str, buttons=None):
        """Sends the first prompt, then edits the same message for every step.

        If the command was outgoing (event.out), edits that message.
        Otherwise replies to it. Subsequent steps edit the stored message.
        """
        try:
            if self._message_id is not None:
                await self.event.client.edit_message(self.chat, self._message_id, text, buttons=buttons)
            else:
                if getattr(self.event, "out", False):
                    await self.event.client.edit_message(self.chat, self.event.id, text, buttons=buttons)
                    self._message_id = self.event.id
                else:
                    msg = await self.event.client.send_message(self.chat, text, buttons=buttons)
                    self._message_id = getattr(msg, "id", None)
        except Exception as e:
            log.error(f"TelegramDialog reply error: {e}")
            try:
                msg = await self.event.client.send_message(self.chat, text, buttons=buttons)
                self._message_id = getattr(msg, "id", None)
            except Exception as e2:
                log.error(f"TelegramDialog reply fallback error: {e2}")

    async def _fail(self, text: str):
        await self._reply(text)
        await asyncio.sleep(1.5)
        await self.stop()

    def _media_buttons(self):
        return [
            [Button.text("photo"), Button.text("video"), Button.text("document"), Button.text("audio")],
            [Button.text("sticker"), Button.text("gif"), Button.text("voice"), Button.text("video_note")],
            [Button.text(self._t("messages.dialog_skip", "Skip")), Button.text(self._t("messages.dialog_cancel", "Cancel"))],
        ]

    def _is_cancel(self, text: str) -> bool:
        lowered = (text or "").strip().lower()
        return lowered in (".cancel", "cancel", self._t("messages.dialog_cancel", "Cancel").lower(), self._t("messages.cancel", "Cancelled").lower())

    # ------------------------------------------------------------------
    # Step: chats
    # ------------------------------------------------------------------
    async def _step_chats(self):
        self.state["step"] = "chats"
        prompt = self._t("messages.dialog_chats_prompt", "Enter chats (comma-separated: @user, 12345)")
        buttons = [[Button.text(self._t("messages.dialog_cancel", "Cancel"))]]
        await self._reply(prompt, buttons=buttons)

    async def _handle_chats(self, text: str):
        chats = [c.strip() for c in text.split(",") if c.strip()]
        if not chats:
            await self._fail(self._t("messages.chat_empty", "Chat cannot be empty"))
            return
        self.state["chats"] = chats
        added = self._t(
            "messages.dialog_chats_added", "Added {count} chat(s): {chats}",
            count=len(chats), chats=", ".join(chats[:3]),
        )
        await self._reply(added)
        await self._step_media_type()

    # ------------------------------------------------------------------
    # Step: media type
    # ------------------------------------------------------------------
    async def _step_media_type(self):
        self.state["step"] = "media_type"
        prompt = self._t("messages.dialog_media_type_prompt", "Enter media type:") + "\n" + \
            self._t("messages.dialog_media_types", "Media types: photo, video, document, audio, sticker, gif, voice, video_note") + "\n" + \
            self._t("messages.dialog_media_type_hint", "(or type n to skip)")
        await self._reply(prompt, buttons=self._media_buttons())

    async def _handle_media_type(self, text: str):
        lowered = text.strip().lower()
        if lowered in ("n", "skip", "no"):
            await self._step_rotation()
            return
        normalized = _normalize_media_type(text)
        if normalized is None:
            await self._fail(self._t("messages.dialog_media_invalid_type", "Invalid media type"))
            return
        self.state["media_type"] = normalized
        self.state["awaiting_upload"] = True
        await self._step_media_path(normalized)

    async def _step_media_path(self, media_type: str):
        self.state["step"] = "media_path"
        prompt = self._t("messages.dialog_media_upload", "Send a file or type its path") + "\n" + self._t("messages.dialog_media_upload_hint", "(or type n to skip)")
        buttons = [[Button.text(self._t("messages.dialog_cancel", "Cancel"))]]
        await self._reply(prompt, buttons=buttons)

    async def _handle_media_path(self, event, text: Optional[str] = None, media_path: Optional[str] = None):
        self.state["awaiting_upload"] = False
        media_type = self.state["media_type"]
        path = media_path
        if path is None and text:
            path = text.strip().strip('"').strip("'")

        if not path or path.lower() in ("n", "skip", "no"):
            await self._step_rotation()
            return

        if not os.path.exists(path):
            await self._fail(self._t("messages.dialog_media_file_not_found", "File not found: {path}", path=path))
            return

        if media_type is None:
            media_type = _detect_media_type_from_file(path) or "document"
            self.state["media_type"] = media_type

        try:
            saved = gallery_service.save_file(path, _gallery_category(media_type))
        except Exception as e:
            log.error(f"Gallery save error: {e}")
            saved = path

        self.state["media_path"] = saved
        await self._reply(self._t("messages.dialog_media_saved_path", "Media saved: {path}", path=saved))
        await self._step_rotation()

    # ------------------------------------------------------------------
    # Step: rotation
    # ------------------------------------------------------------------
    async def _step_rotation(self):
        self.state["step"] = "rotation"
        prompt = self._t("messages.dialog_rotation_prompt", "Add rotation messages? (y/n)")
        buttons = [
            [Button.text("y"), Button.text("n")],
            [Button.text(self._t("messages.dialog_cancel", "Cancel"))],
        ]
        await self._reply(prompt, buttons=buttons)

    async def _handle_rotation(self, text: str):
        choice = text.strip().lower()
        if choice in ("n", "no", "skip"):
            await self._step_text()
            return
        if choice in ("y", "yes", "1"):
            self.state["rotation_mode"] = True
            self.state["step"] = "rotation_collect"
            prompt = self._t(
                "messages.dialog_rotation_messages",
                "Enter messages (send each as a separate message, or type .done to finish)",
            )
            buttons = [[Button.text(self._t("messages.dialog_cancel", "Cancel"))]]
            await self._reply(prompt, buttons=buttons)
            return
        if choice == "2":
            await self._reply(self._t("messages.rotation_editor", "Open editor"))
            await self._step_text()
            return
        # Any other text is treated as the first rotation message.
        self.state["rotation_mode"] = True
        self.state["step"] = "rotation_collect"
        self.state["messages_list"].append(text)
        prompt = self._t(
            "messages.dialog_rotation_messages",
            "Enter messages (send each as a separate message, or type /done to finish)",
        )
        buttons = [[Button.text(self._t("messages.dialog_cancel", "Cancel"))]]
        await self._reply(prompt, buttons=buttons)

    async def _handle_rotation_message(self, text: str):
        if text.strip().lower() in (".done", ">>"):
            msgs = self.state["messages_list"]
            if not msgs:
                await self._fail(self._t("messages.dialog_rotation_no_messages", "No messages entered"))
                return
            if self.mode == "edit" and len(msgs) > 1:
                self.db.update_task(self.task_id, messages_list=msgs, message=msgs[0])
                self.db.update_message_index(self.task_id, 0)
                self.state["need_restart"] = True
            await self._reply(self._t("messages.dialog_rotation_saved", "Rotation saved: {count} messages", count=len(msgs)))
            self.state["rotation_mode"] = False
            if self.mode == "edit":
                await self._step_edit_media()
            else:
                await self._step_interval()
            return
        self.state["messages_list"].append(text)
        await self._reply(self._t("messages.rotation_saved", "Message #{num} saved", num=len(self.state["messages_list"])))

    # ------------------------------------------------------------------
    # Step: text
    # ------------------------------------------------------------------
    async def _step_text(self):
        self.state["step"] = "text"
        if self.state["media_path"]:
            prompt = self._t("messages.dialog_text_prompt", "Enter text (n to skip)")
            buttons = [[Button.text("n"), Button.text(self._t("messages.dialog_cancel", "Cancel"))]]
        else:
            prompt = self._t("messages.dialog_text_enter", "Enter text")
            buttons = [
                [Button.text("1"), Button.text("2")],
                [Button.text(self._t("messages.dialog_cancel", "Cancel"))],
            ]
        await self._reply(prompt, buttons=buttons)

    async def _handle_text(self, event, text: str = None):
        if text is None:
            text = self._extract_text_with_entities(event)
        choice = (text or "").strip().lower()
        if self.state.get("media_path"):
            if choice in ("n", "no", "skip"):
                await self._step_once()
                return
            # Any other text is treated as the message body directly.
            self.state["message"] = text
            await self._step_once()
            return

        if choice == "1":
            self.state["step"] = "text_collect"
            prompt = self._t("messages.text_console", "Enter text (>> to finish)")
            buttons = [[Button.text(self._t("messages.dialog_cancel", "Cancel"))]]
            await self._reply(prompt, buttons=buttons)
        elif choice == "2":
            await self._reply(self._t("messages.editor_help", "Enter text (HTML supported)"))
            await self._step_once()
        else:
            # Direct text input — accept it as the message body.
            self.state["message"] = text
            await self._step_once()

    def _extract_text_with_entities(self, event):
        text = getattr(event, 'raw_text', None)
        msg = getattr(event, 'message', None)
        if text is None and msg is not None:
            text = getattr(msg, 'text', '') or getattr(msg, 'message', '') or ''
        if msg is not None and text:
            try:
                entities = getattr(msg, 'entities', None)
                if not entities:
                    entities = getattr(msg, 'entities', None)
                if not entities:
                    entities = getattr(msg, '_entities', None)
                # Also check for formatting_entities
                if not entities:
                    entities = getattr(msg, 'formatting_entities', None)
                if entities and text:
                    def to_utf16(s):
                        return s.encode('utf-16-le')

                    def from_utf16(b):
                        return b.decode('utf-16-le')

                    text_utf16 = to_utf16(text)
                    for entity in sorted(entities, key=lambda e: getattr(e, 'offset', 0), reverse=True):
                        start = getattr(entity, 'offset', 0) * 2
                        end = (getattr(entity, 'offset', 0) + getattr(entity, 'length', 0)) * 2
                        if start < 0 or end > len(text_utf16):
                            continue
                        inner = from_utf16(text_utf16[start:end])
                        cls_name = type(entity).__name__
                        if isinstance(entity, MessageEntityBold) or cls_name == 'MessageEntityBold':
                            tag = f"<b>{inner}</b>"
                        elif isinstance(entity, MessageEntityItalic) or cls_name == 'MessageEntityItalic':
                            tag = f"<i>{inner}</i>"
                        elif isinstance(entity, MessageEntityUnderline) or cls_name == 'MessageEntityUnderline':
                            tag = f"<u>{inner}</u>"
                        elif isinstance(entity, MessageEntityStrike) or cls_name == 'MessageEntityStrike':
                            tag = f"<s>{inner}</s>"
                        elif isinstance(entity, MessageEntityCode) or cls_name == 'MessageEntityCode':
                            tag = f"<code>{inner}</code>"
                        elif isinstance(entity, MessageEntityPre) or cls_name == 'MessageEntityPre':
                            tag = f"<pre>{inner}</pre>"
                        elif isinstance(entity, MessageEntitySpoiler) or cls_name == 'MessageEntitySpoiler':
                            tag = f"<span class=\"tg-spoiler\">{inner}</span>"
                        elif isinstance(entity, MessageEntityBlockquote) or cls_name == 'MessageEntityBlockquote':
                            tag = f"<blockquote>{inner}</blockquote>"
                        elif isinstance(entity, MessageEntityTextUrl) or cls_name == 'MessageEntityTextUrl':
                            tag = f"<a href=\"{getattr(entity, 'url', '')}\">{inner}</a>"
                        elif isinstance(entity, MessageEntityUrl) or cls_name == 'MessageEntityUrl':
                            tag = f"<a href=\"{inner}\">{inner}</a>"
                        elif isinstance(entity, MessageEntityMention) or cls_name == 'MessageEntityMention':
                            tag = f"<a href=\"https://t.me/{inner.lstrip('@')}\">{inner}</a>"
                        elif isinstance(entity, MessageEntityEmail) or cls_name == 'MessageEntityEmail':
                            tag = f"<a href=\"mailto:{inner}\">{inner}</a>"
                        elif isinstance(entity, MessageEntityCustomEmoji) or cls_name == 'MessageEntityCustomEmoji':
                            doc_id = getattr(entity, 'document_id', '') or getattr(entity, 'document_id', '')
                            tag = f"<tg-emoji emoji-id=\"{doc_id}\">{inner}</tg-emoji>"
                        elif isinstance(entity, MessageEntityBotCommand) or cls_name == 'MessageEntityBotCommand':
                            tag = f"<code>{inner}</code>"
                        elif 'CustomEmoji' in cls_name and hasattr(entity, 'document_id'):
                            tag = f"<tg-emoji emoji-id=\"{getattr(entity, 'document_id')}\">{inner}</tg-emoji>"
                        elif hasattr(entity, 'document_id') and 'Emoji' in cls_name:
                            tag = f"<tg-emoji emoji-id=\"{getattr(entity, 'document_id')}\">{inner}</tg-emoji>"
                        else:
                            tag = inner
                        text_utf16 = text_utf16[:start] + to_utf16(tag) + text_utf16[end:]
                    text = from_utf16(text_utf16)
            except Exception:
                pass
        return text or ""

    async def _handle_text_collect(self, event, text: str = None):
        if text is None:
            text = self._extract_text_with_entities(event)
        else:
            try:
                entities = getattr(getattr(event, 'message', None), 'entities', None)
                if entities and text:
                    def to_utf16(s):
                        return s.encode('utf-16-le')

                    def from_utf16(b):
                        return b.decode('utf-16-le')

                    text_utf16 = to_utf16(text)
                    for entity in sorted(entities, key=lambda e: e.offset, reverse=True):
                        start = entity.offset * 2
                        end = (entity.offset + entity.length) * 2
                        if start < 0 or end > len(text_utf16):
                            continue
                        inner = from_utf16(text_utf16[start:end])
                        if isinstance(entity, MessageEntityBold):
                            tag = f"<b>{inner}</b>"
                        elif isinstance(entity, MessageEntityItalic):
                            tag = f"<i>{inner}</i>"
                        elif isinstance(entity, MessageEntityUnderline):
                            tag = f"<u>{inner}</u>"
                        elif isinstance(entity, MessageEntityStrike):
                            tag = f"<s>{inner}</s>"
                        elif isinstance(entity, MessageEntityCode):
                            tag = f"<code>{inner}</code>"
                        elif isinstance(entity, MessageEntityPre):
                            tag = f"<pre>{inner}</pre>"
                        elif isinstance(entity, MessageEntitySpoiler):
                            tag = f"<span class=\"tg-spoiler\">{inner}</span>"
                        elif isinstance(entity, MessageEntityBlockquote):
                            tag = f"<blockquote>{inner}</blockquote>"
                        elif isinstance(entity, MessageEntityTextUrl):
                            tag = f"<a href=\"{entity.url}\">{inner}</a>"
                        elif isinstance(entity, MessageEntityUrl):
                            tag = f"<a href=\"{inner}\">{inner}</a>"
                        elif isinstance(entity, MessageEntityMention):
                            tag = f"<a href=\"https://t.me/{inner.lstrip('@')}\">{inner}</a>"
                        elif isinstance(entity, MessageEntityEmail):
                            tag = f"<a href=\"mailto:{inner}\">{inner}</a>"
                        elif isinstance(entity, MessageEntityCustomEmoji):
                            tag = f"<tg-emoji emoji-id=\"{entity.document_id}\">{inner}</tg-emoji>"
                        elif isinstance(entity, MessageEntityBotCommand):
                            tag = f"<code>{inner}</code>"
                        else:
                            tag = inner
                        text_utf16 = text_utf16[:start] + to_utf16(tag) + text_utf16[end:]
                    text = from_utf16(text_utf16)
            except Exception:
                pass
        # No ">>" needed — the first message is the full text.
        if self.task_id:
            self.state["message"] = text
            self.db.update_task(self.task_id, message=text)
            self.state["need_restart"] = True
            await self._reply(self._t("messages.text_updated", "Text updated!"))
            await self._step_edit_rotation()
            return
        self.state["message"] = text
        await self._step_once()

    # ------------------------------------------------------------------
    # Step: once
    # ------------------------------------------------------------------
    async def _step_once(self):
        self.state["step"] = "once"
        prompt = self._t("messages.dialog_once_prompt", "One-time task? (date time / +30m / +2h / n to skip)")
        buttons = [
            [Button.text("n"), Button.text(self._t("messages.dialog_cancel", "Cancel"))],
        ]
        await self._reply(prompt, buttons=buttons)

    async def _handle_once(self, text: str):
        raw = text.strip().lower()
        if raw in ("n", "skip"):
            await self._step_interval()
            return
        if self.scheduler:
            once_dt = self.scheduler.parse_once(text.strip())
            if once_dt:
                self.state["is_once"] = 1
                self.state["once_time"] = once_dt.strftime("%Y-%m-%d %H:%M:%S")
                await self._reply(self._t("messages.once_scheduled", "One-time task on {time}", time=self.state["once_time"]))
                await self._step_run_now()
                return
        await self._fail(self._t("messages.dialog_once_invalid", "Invalid once time format"))

    # ------------------------------------------------------------------
    # Step: interval
    # ------------------------------------------------------------------
    async def _step_interval(self):
        self.state["step"] = "interval"
        formats = self._t("messages.dialog_interval_formats", "Formats: 30, 5m, 2h, 1d | 25m10s | 60-120 | 14:10@1,3,5")
        prompt = self._t("messages.dialog_interval_prompt", "Interval (e.g. 5m, 1h, 10-30, 14:30@1,3,5)") + "\n" + formats
        buttons = [[Button.text(self._t("messages.dialog_cancel", "Cancel"))]]
        await self._reply(prompt, buttons=buttons)

    async def _handle_interval(self, text: str):
        raw = text.strip()
        if not raw:
            await self._fail(self._t("messages.dialog_interval_invalid", "Invalid interval format"))
            return
        if self.scheduler:
            cfg = self.scheduler.parse_interval(raw)
            if cfg:
                self.state["interval"] = cfg
                await self._reply(self._t("messages.interval_info", "Interval: {interval}", interval=self.scheduler.format_interval(cfg)))
                await self._step_run_now()
                return
        await self._fail(self._t("messages.dialog_interval_invalid", "Invalid interval format"))

    # ------------------------------------------------------------------
    # Step: run now
    # ------------------------------------------------------------------
    async def _step_run_now(self):
        self.state["step"] = "run_now"
        summary = self._build_summary()
        prompt = summary + "\n\n" + self._t("messages.dialog_run_now", "Start now? (y/n)")
        buttons = [
            [Button.text("y"), Button.text("n")],
            [Button.text(self._t("messages.dialog_cancel", "Cancel"))],
        ]
        await self._reply(prompt, buttons=buttons)

    def _build_summary(self) -> str:
        s = self.state
        title = self._t("messages.dialog_summary_title", "Task summary")
        chats = ", ".join(s["chats"][:3])
        if len(s["chats"]) > 3:
            chats += self._t("messages.and_more", "... and {count} more").replace("{count}", str(len(s["chats"]) - 3))
        chats_line = self._t("messages.dialog_summary_chats", "Chats: {chats}", chats=chats)

        if len(s["messages_list"]) > 1:
            text_line = self._t("messages.dialog_summary_rotation", "Rotation: {count} messages", count=len(s["messages_list"]))
        else:
            preview = (s["message"] or "")[:50].replace("\n", " ")
            text_line = self._t("messages.dialog_summary_text", "Text: {text}", text=preview or self._t("messages.empty_text", "(empty)"))

        if s["media_path"]:
            media_line = self._t("messages.dialog_summary_media", "Media: {media}", media=_media_label(s["media_type"]))
        else:
            media_line = self._t("messages.dialog_summary_media", "Media: {media}", media=self._t("messages.no_media", "None"))

        if s["is_once"]:
            interval_line = self._t("messages.dialog_summary_once", "Once: {time}", time=s["once_time"] or "—")
        else:
            interval_line = self._t("messages.dialog_summary_interval", "Interval: {interval}", interval=self.scheduler.format_interval(s["interval"]) if self.scheduler else "—")

        mode = self._t("messages.dialog_created", "Task #{tid} created", tid="?") if self.mode == "add" else self._t("messages.dialog_updated", "Task #{tid} updated", tid=self.task_id or "?")
        return f"*{title}*\n{chats_line}\n{text_line}\n{media_line}\n{interval_line}\n\n{mode}"

    async def _handle_run_now(self, text: str):
        choice = text.strip().lower()
        if choice in ("y", "yes"):
            await self._commit(start=True)
        elif choice in ("n", "no"):
            await self._commit(start=False)
        else:
            await self._fail(self._t("messages.wrong_choice", "Wrong choice"))

    # ------------------------------------------------------------------
    # Commit / create or update task
    # ------------------------------------------------------------------
    async def _commit(self, start: bool):
        self.state["step"] = "commit"
        s = self.state
        chats = s["chats"]
        if not chats:
            await self._fail(self._t("messages.chat_empty", "Chat cannot be empty"))
            return

        msg = s["message"] or ""
        msgs_list = s["messages_list"] if len(s["messages_list"]) > 1 else None
        if msgs_list:
            msg = msgs_list[0]

        if not msg and not s["media_path"]:
            await self._fail(self._t("messages.dialog_text_empty", "Text cannot be empty without media"))
            return

        cfg = s["interval"] or {"type": "interval", "seconds": 60}

        try:
            if self.mode == "add":
                tid = self.db.add(
                    chat_id=chats[0],
                    message=msg,
                    media_path=s["media_path"],
                    media_type=s["media_type"],
                    interval_config=cfg,
                    is_once=s["is_once"],
                    once_time=s["once_time"],
                    messages_list=msgs_list,
                    chats_list=chats,
                )
                if s["is_once"] or start:
                    if self.task_service:
                        await self.task_service.start_task(tid)
                msg_text = self._t("messages.dialog_created", "Task #{tid} created", tid=tid)
            else:
                tid = self.task_id
                self.db.update_task(
                    tid,
                    chat_id=chats[0],
                    message=msg,
                    media_path=s["media_path"],
                    media_type=s["media_type"],
                    interval_config=cfg,
                    is_once=s["is_once"] if s["is_once"] else None,
                    once_time=s["once_time"],
                    messages_list=msgs_list,
                    chats_list=chats,
                )
                if self.task_service:
                    running = getattr(self.task_service, "running", {})
                    if tid in running:
                        await self.task_service.stop_task(tid)
                        await asyncio.sleep(0.3)
                        if start or s["is_once"]:
                            await self.task_service.start_task(tid)
                msg_text = self._t("messages.dialog_updated", "Task #{tid} updated", tid=tid)
        except Exception as e:
            log.error(f"TelegramDialog commit error: {e}")
            await self._fail(self._t("messages.dialog_media_upload_error", "Upload error: {error}", error=str(e)))
            return

        log.info(f"TelegramDialog {self.mode} task #{tid}")
        await self._reply(msg_text)
        await asyncio.sleep(1.0)
        await self.stop()

    # ------------------------------------------------------------------
    # Message dispatcher
    # ------------------------------------------------------------------
    async def _on_message(self, event):
        step = self.state["step"]

        if self.state["awaiting_upload"]:
            media_path = await self._extract_media(event)
            step = self.state["step"]
            if step == "edit_media_path":
                await self._handle_edit_media_path(event, media_path=media_path)
                return
            if media_path:
                await self._handle_media_path(event, media_path=media_path)
                return
            text = event.raw_text
            if self._is_cancel(text):
                await self._reply(self._t("messages.cancel", "Cancelled"))
                await self.stop()
                return
            if step == "edit_media_path":
                await self._handle_edit_media_path(event, text=text)
                return
            await self._handle_media_path(event, text=text)
            return

        if self.state["rotation_mode"]:
            text = getattr(event, 'raw_text', '') or ''
            await self._handle_rotation_message(text)
            return

        if step == "text_collect":
            await self._handle_text_collect(event)
            return
        if step == "edit_text_input":
            await self._handle_edit_text_input(event)
            return

        text = self._extract_text_with_entities(event)
        lowered = (text or "").strip().lower()

        if lowered in (".cancel", "cancel", self._t("messages.dialog_cancel", "Cancel").lower()):
            await self._reply(self._t("messages.cancel", "Cancelled"))
            await self.stop()
            return

        handlers = {
            "chats": self._handle_chats,
            "media_type": self._handle_media_type,
            "rotation": self._handle_rotation,
            "text": self._handle_text,
            "once": self._handle_once,
            "interval": self._handle_interval,
            "run_now": self._handle_run_now,
            "edit_id": self._handle_edit_id,
            "edit_chats": self._handle_edit_chats,
            "edit_text": self._handle_edit_text,
            "edit_text_method": self._handle_edit_text_method,
            "edit_rotation": self._handle_edit_rotation,
            "edit_rotation_method": self._handle_edit_rotation_method,
            "edit_rotation_editor": self._handle_edit_rotation_editor,
            "rotation_edit_msg": self._handle_rotation_edit_msg,
            "edit_media": self._handle_edit_media,
            "edit_media_delete": self._handle_edit_media_delete,
            "edit_media_type": self._handle_edit_media_type,
            "edit_interval": self._handle_edit_interval,
        }
        handler = handlers.get(step)
        if handler is None:
            return
        if step == "edit_text_input":
            await self._handle_edit_text_input(event)
            return
        if step == "text":
            await self._handle_text(event)
            return
        await handler(text)

    async def _extract_media(self, event) -> Optional[str]:
        media = getattr(event, "media", None)
        if media is None:
            return None
        try:
            return await event.message.download_media(
                file=str(gallery_service.ROOT / "other") + "/"
            )
        except Exception as e:
            log.error(f"TelegramDialog media download error: {e}")
            return None

    # ------------------------------------------------------------------
    # Edit flow
    # ------------------------------------------------------------------
    async def _start_edit(self):
        if self.task_id is None:
            self.state["step"] = "edit_id"
            prompt = self._t("messages.enter_task_id_edit", "Task ID to edit: ")
            buttons = [[Button.text(self._t("messages.dialog_cancel", "Cancel"))]]
            await self._reply(prompt, buttons=buttons)
            return
        await self._load_edit_task()

    async def _handle_edit_id(self, text: str):
        try:
            tid = int(text.strip())
        except ValueError:
            await self._fail(self._t("messages.wrong_format", "Invalid format"))
            return
        if not self.db.get_full(tid):
            await self._fail(self._t("messages.dialog_task_not_found", "Task #{tid} not found", tid=tid))
            return
        self.task_id = tid
        await self._load_edit_task()

    async def _load_edit_task(self):
        task = self.db.get_full(self.task_id)
        if not task:
            await self._fail(self._t("messages.dialog_task_not_found", "Task #{tid} not found", tid=self.task_id))
            return

        chats = task.get("chats_list") or [task.get("chat_id")]
        self.state["chats"] = [str(c) for c in chats]
        self.state["message"] = task.get("message") or ""
        self.state["media_path"] = task.get("media_path")
        self.state["media_type"] = task.get("media_type")
        self.state["interval"] = task.get("interval_config") or {"type": "interval", "seconds": 60}
        self.state["is_once"] = 1 if task.get("is_once") else 0
        self.state["once_time"] = task.get("once_time")
        msgs = task.get("messages_list") or []
        self.state["messages_list"] = list(msgs)
        self.state["rotation_mode"] = False
        self.state["need_restart"] = False

        await self._reply(self._t("messages.editor_open", "Editor opened"))
        await self._step_edit_chats()

    # ------------------------------------------------------------------
    # Edit flow (mirrors console.py edit_task)
    # ------------------------------------------------------------------
    async def _step_edit_chats(self):
        self.state["step"] = "edit_chats"
        chats_display = ", ".join(self.state["chats"][:3])
        prompt = self._t("messages.edit_chat", "Current chat: {chat}").replace("{chat}", chats_display)
        prompt += "\n" + self._t("messages.edit_chat_new", "New chat(s) (Enter to keep): ")
        buttons = [[Button.text(self._t("messages.dialog_cancel", "Cancel"))]]
        await self._reply(prompt, buttons=buttons)

    async def _handle_edit_chats(self, text: str):
        if text.strip():
            new_chats = [c.strip() for c in text.split(",") if c.strip()]
            if new_chats:
                self.state["chats"] = new_chats
                self.db.update_task(self.task_id, chat_id=new_chats[0], chats_list=new_chats)
                self.state["need_restart"] = True
                updated = self._t("messages.edit_chat_updated", "Chats updated! {count} chat(s): {chats}") \
                    .replace("{count}", str(len(new_chats))).replace("{chats}", ", ".join(new_chats[:3]))
                await self._reply(updated)
        # When rotation messages exist, the text step is skipped (mirrors console).
        if len(self.state["messages_list"]) > 1:
            await self._step_edit_rotation()
        else:
            await self._step_edit_text()

    async def _step_edit_text(self):
        self.state["step"] = "edit_text"
        current = self.state["message"] or self._t("messages.empty_text", "(empty)")
        prompt = self._t("messages.edit_text", "Current text:") + "\n" + current[:300]
        prompt += "\n" + self._t("messages.edit_text_change", "Change text? (y/n): ")
        buttons = [[Button.text("y"), Button.text("n")], [Button.text(self._t("messages.dialog_cancel", "Cancel"))]]
        await self._reply(prompt, buttons=buttons)

    async def _handle_edit_text(self, text: str):
        choice = text.strip().lower()
        if choice not in ("y", "yes"):
            await self._step_edit_rotation()
            return
        has_media = bool(self.state["media_path"])
        if not has_media:
            # Simple task — ask for the new text on the next step.
            self.state["step"] = "edit_text_input"
            prompt = self._t("messages.edit_text_input", "Enter new text:")
            buttons = [[Button.text(self._t("messages.dialog_cancel", "Cancel"))]]
            await self._reply(prompt, buttons=buttons)
            return
        # Media present — offer replace / delete / keep.
        prompt = self._t("messages.edit_text_method", "How to enter new text?") + "\n"
        prompt += self._t("messages.edit_text_method_replace", "1 - Replace text") + "\n"
        prompt += self._t("messages.edit_text_method_delete", "2 - Delete text (leave empty)")
        buttons = [[Button.text("1"), Button.text("2")],
                   [Button.text(self._t("messages.dialog_cancel", "Cancel"))]]
        self.state["step"] = "edit_text_method"
        await self._reply(prompt, buttons=buttons)

    async def _handle_edit_text_input(self, event, text: str = None):
        if text is None:
            text = self._extract_text_with_entities(event)
        else:
            try:
                entities = getattr(getattr(event, 'message', None), 'entities', None)
                if entities and text:
                    def to_utf16(s):
                        return s.encode('utf-16-le')

                    def from_utf16(b):
                        return b.decode('utf-16-le')

                    text_utf16 = to_utf16(text)
                    for entity in sorted(entities, key=lambda e: e.offset, reverse=True):
                        start = entity.offset * 2
                        end = (entity.offset + entity.length) * 2
                        if start < 0 or end > len(text_utf16):
                            continue
                        inner = from_utf16(text_utf16[start:end])
                        if isinstance(entity, MessageEntityBold):
                            tag = f"<b>{inner}</b>"
                        elif isinstance(entity, MessageEntityItalic):
                            tag = f"<i>{inner}</i>"
                        elif isinstance(entity, MessageEntityUnderline):
                            tag = f"<u>{inner}</u>"
                        elif isinstance(entity, MessageEntityStrike):
                            tag = f"<s>{inner}</s>"
                        elif isinstance(entity, MessageEntityCode):
                            tag = f"<code>{inner}</code>"
                        elif isinstance(entity, MessageEntityPre):
                            tag = f"<pre>{inner}</pre>"
                        elif isinstance(entity, MessageEntitySpoiler):
                            tag = f"<span class=\"tg-spoiler\">{inner}</span>"
                        elif isinstance(entity, MessageEntityBlockquote):
                            tag = f"<blockquote>{inner}</blockquote>"
                        elif isinstance(entity, MessageEntityTextUrl):
                            tag = f"<a href=\"{entity.url}\">{inner}</a>"
                        elif isinstance(entity, MessageEntityUrl):
                            tag = f"<a href=\"{inner}\">{inner}</a>"
                        elif isinstance(entity, MessageEntityMention):
                            tag = f"<a href=\"https://t.me/{inner.lstrip('@')}\">{inner}</a>"
                        elif isinstance(entity, MessageEntityEmail):
                            tag = f"<a href=\"mailto:{inner}\">{inner}</a>"
                        elif isinstance(entity, MessageEntityCustomEmoji):
                            tag = f"<tg-emoji emoji-id=\"{entity.document_id}\">{inner}</tg-emoji>"
                        elif isinstance(entity, MessageEntityBotCommand):
                            tag = f"<code>{inner}</code>"
                        else:
                            tag = inner
                        text_utf16 = text_utf16[:start] + to_utf16(tag) + text_utf16[end:]
                    text = from_utf16(text_utf16)
            except Exception:
                pass
        if (text or "").strip().lower() in ("cancel", "отмена", "назад"):
            await self._step_edit_rotation()
            return
        self.state["message"] = text
        self.db.update_task(self.task_id, message=text)
        self.state["need_restart"] = True
        await self._reply(self._t("messages.text_updated", "Text updated!"))
        await self._step_edit_rotation()

    async def _handle_edit_text_method(self, text: str):
        choice = text.strip().lower()
        if choice in ("1", "replace", "змінити", "замінити"):
            self.state["step"] = "edit_text_input"
            await self._reply(self._t("messages.enter_new_text", "Enter new text:"))
            return
        elif choice in ("2", "3", "delete", "видалити") and self.state["media_path"]:
            self.state["message"] = ""
            self.db.update_task(self.task_id, message="")
            self.state["need_restart"] = True
            await self._reply(self._t("messages.text_deleted", "Text deleted!"))
            await self._step_edit_rotation()
        elif choice in ("cancel", "отмена", "назад"):
            await self._step_edit_rotation()
            return
        else:
            await self._fail(self._t("messages.wrong_choice", "Wrong choice"))

    async def _step_edit_rotation(self):
        self.state["step"] = "edit_rotation"
        current = self.state["messages_list"]
        if not current:
            # No rotation messages — skip straight to media (mirrors console).
            await self._step_edit_media()
            return
        preview = ", ".join(m.replace("\n", " ")[:30] for m in current[:3])
        prompt = self._t("task.rotation", "Current rotation") + f": {len(current)} " + \
                 self._t("task.messages", "messages") + "\n" + preview
        prompt += "\n" + self._t("messages.rotation_change", "Change rotation messages? (y/n): ")
        buttons = [[Button.text("y"), Button.text("n")], [Button.text(self._t("messages.dialog_cancel", "Cancel"))]]
        await self._reply(prompt, buttons=buttons)

    async def _handle_edit_rotation(self, text: str):
        choice = text.strip().lower()
        if choice not in ("y", "yes"):
            await self._step_edit_media()
            return
        prompt = self._t("messages.rotation_new", "Enter new rotation messages") + "\n"
        prompt += self._t("messages.rotation_replace", "1 - Replace all (enter messages one by one)") + "\n"
        prompt += self._t("messages.rotation_edit", "2 - Edit existing messages") + "\n"
        prompt += self._t("messages.rotation_delete", "3 - Delete rotation (keep first message only)")
        buttons = [[Button.text("1"), Button.text("2"), Button.text("3")],
                   [Button.text(self._t("messages.dialog_cancel", "Cancel"))]]
        self.state["step"] = "edit_rotation_method"
        await self._reply(prompt, buttons=buttons)

    async def _handle_edit_rotation_method(self, text: str):
        choice = text.strip().lower()
        current = self.state["messages_list"]
        if choice == "3":
            first = current[0] if current else (self.state["message"] or "")
            self.state["messages_list"] = []
            self.state["message"] = first
            self.db.update_task(self.task_id, messages_list=None, message=first)
            self.db.update_message_index(self.task_id, 0)
            self.state["need_restart"] = True
            await self._reply(self._t("messages.rotation_deleted", "Rotation deleted! Kept: {msg}").replace("{msg}", first[:50]))
            await self._step_edit_media()
            return
        if choice == "1":
            # Replace existing rotation with new messages entered one by one.
            self.state["messages_list"] = []
            self.db.update_message_index(self.task_id, 0)
            self.state["rotation_mode"] = True
            self.state["step"] = "rotation_collect"
            prompt = self._t("messages.dialog_rotation_messages", "Enter messages (send each as a separate message, .done to finish)")
            buttons = [[Button.text(self._t("messages.dialog_cancel", "Cancel"))]]
            await self._reply(prompt, buttons=buttons)
        elif choice == "2":
            await self._step_edit_rotation_editor()
        else:
            await self._fail(self._t("messages.wrong_choice", "Wrong choice"))

    async def _step_edit_media(self):
        self.state["step"] = "edit_media"
        if not self.state["media_path"]:
            # No media yet — offer to add one, otherwise skip straight to interval.
            prompt = self._t("messages.media_add", "Add media? (y/n)")
            buttons = [[Button.text("y"), Button.text("n")], [Button.text(self._t("messages.dialog_cancel", "Cancel"))]]
            await self._reply(prompt, buttons=buttons)
            return
        current = f"{os.path.basename(self.state['media_path'])} ({_media_label(self.state['media_type'])})"
        prompt = self._t("task.media", "Current media") + ": " + current
        prompt += "\n" + self._t("messages.media_change", "Change media? (y/n): ")
        buttons = [[Button.text("y"), Button.text("n")], [Button.text(self._t("messages.dialog_cancel", "Cancel"))]]
        await self._reply(prompt, buttons=buttons)

    async def _handle_edit_media(self, text: str):
        choice = text.strip().lower()
        types = self._t("messages.dialog_media_types", "Media types: photo, video, document, audio, sticker, gif, voice, video_note")
        if not self.state["media_path"]:
            if choice not in ("y", "yes"):
                await self._step_edit_interval()
                return
            # Adding new media — go straight to type selection.
        elif choice in ("y", "yes"):
            # Changing existing media — ask delete or replace.
            buttons = [
                [Button.text("d"), Button.text("r")],
                [Button.text(self._t("messages.dialog_cancel", "Cancel"))],
            ]
            prompt = self._t("messages.media_change", "Current media exists. Delete (d) or replace (r)?")
            self.state["step"] = "edit_media_delete"
            await self._reply(prompt, buttons=buttons)
            return
        else:
            await self._step_edit_interval()
            return
        prompt = self._t("messages.media_enter_type", "New media type: ") + "\n" + types
        buttons = self._media_buttons()
        self.state["step"] = "edit_media_type"
        await self._reply(prompt, buttons=buttons)

    async def _handle_edit_media_delete(self, text: str):
        choice = text.strip().lower()
        if choice in ("d", "delete"):
            self.state["media_path"] = None
            self.state["media_type"] = None
            self.db.update_task(self.task_id, media_path=None, media_type=None)
            self.state["need_restart"] = True
            await self._reply(self._t("messages.media_deleted", "Media deleted!"))
            await self._step_edit_interval()
            return
        if choice in ("r", "replace"):
            types = self._t("messages.dialog_media_types", "Media types: photo, video, document, audio, sticker, gif, voice, video_note")
            prompt = self._t("messages.media_enter_type", "New media type: ") + "\n" + types
            buttons = self._media_buttons()
            self.state["step"] = "edit_media_type"
            await self._reply(prompt, buttons=buttons)
            return
        await self._fail(self._t("messages.wrong_choice", "Wrong choice"))

    async def _handle_edit_media_type(self, text: str):
        normalized = _normalize_media_type(text)
        if normalized is None:
            await self._fail(self._t("messages.dialog_media_invalid_type", "Invalid media type"))
            return
        self.state["media_type"] = normalized
        self.state["awaiting_upload"] = True
        prompt = self._t("messages.dialog_media_upload", "Send a file or type its path")
        buttons = [[Button.text(self._t("messages.dialog_cancel", "Cancel"))]]
        self.state["step"] = "edit_media_path"
        await self._reply(prompt, buttons=buttons)

    async def _handle_edit_media_path(self, event, text: Optional[str] = None, media_path: Optional[str] = None):
        self.state["awaiting_upload"] = False
        media_type = self.state["media_type"]
        path = media_path
        if path is None and text:
            path = text.strip().strip('"').strip("'")
        if not path:
            await self._fail(self._t("messages.dialog_media_file_not_found", "File not found: {path}", path=text or ""))
            return
        if not os.path.exists(path):
            await self._fail(self._t("messages.dialog_media_file_not_found", "File not found: {path}", path=path))
            return
        if media_type is None:
            media_type = _detect_media_type_from_file(path) or "document"
            self.state["media_type"] = media_type
        try:
            saved = gallery_service.save_file(path, _gallery_category(media_type))
        except Exception as e:
            log.error(f"Gallery save error: {e}")
            saved = path
        self.state["media_path"] = saved
        self.db.update_task(self.task_id, media_path=saved, media_type=media_type)
        self.state["need_restart"] = True
        await self._reply(self._t("messages.media_updated", "Media updated!"))
        await self._step_edit_interval()

    async def _step_edit_interval(self):
        self.state["step"] = "edit_interval"
        current = self.scheduler.format_interval(self.state["interval"]) if self.scheduler else str(self.state["interval"])
        prompt = self._t("messages.edit_interval", "New interval (current: {interval}): ").replace("{interval}", current)
        buttons = [[Button.text(self._t("messages.dialog_cancel", "Cancel"))]]
        await self._reply(prompt, buttons=buttons)

    async def _handle_edit_interval(self, text: str):
        if text.strip() and self.scheduler:
            cfg = self.scheduler.parse_interval(text.strip())
            if cfg:
                self.state["interval"] = cfg
                self.db.update_task(self.task_id, interval_config=cfg)
                self.state["need_restart"] = True
                await self._reply(self._t("messages.interval_info", "Interval: {interval}", interval=self.scheduler.format_interval(cfg)))
        await self._commit_edit()

    async def _commit_edit(self):
        if self.state["need_restart"] and self.task_service:
            running = getattr(self.task_service, "running", {})
            if self.task_id in running:
                await self.task_service.stop_task(self.task_id)
                await self.task_service.start_task(self.task_id)
        await self._reply(self._t("messages.task_updated", "Task #{tid} updated").replace("{tid}", str(self.task_id)))
        log.info(f"TelegramDialog edit task #{self.task_id}")
        await asyncio.sleep(1.0)
        await self.stop()

    # ------------------------------------------------------------------
    # Rotation editor (edit individual messages)
    # ------------------------------------------------------------------
    async def _step_edit_rotation_editor(self):
        self.state["step"] = "edit_rotation_editor"
        msgs = self.state["messages_list"]
        lines = [self._t("messages.rotation_editor_title", "Rotation editor: {count} messages", count=len(msgs))]
        for i, m in enumerate(msgs[:10]):
            preview = m.replace("\n", " ")[:50]
            lines.append(f"#{i+1}: {preview}")
        prompt = "\n".join(lines)
        prompt += "\n\n" + self._t("messages.rotation_editor_actions",
            "Enter message number to edit, 'add' for new, 'del N' to delete, 'done' to finish")
        buttons = [[Button.text("add"), Button.text("done")],
                   [Button.text(self._t("messages.dialog_cancel", "Cancel"))]]
        await self._reply(prompt, buttons=buttons)

    async def _handle_edit_rotation_editor(self, text: str):
        lowered = text.strip().lower()
        if lowered in (".done", "done"):
            await self._step_edit_media()
            return
        if lowered == "add":
            self.state["rotation_mode"] = True
            self.state["step"] = "rotation_collect"
            prompt = self._t("messages.dialog_rotation_messages", "Enter new message (send text directly):")
            buttons = [[Button.text(self._t("messages.dialog_cancel", "Cancel"))]]
            await self._reply(prompt, buttons=buttons)
            return
        # Delete: "del N"
        m = re.match(r"del\s+#?(\d+)", lowered)
        if m:
            idx = int(m.group(1)) - 1
            msgs = self.state["messages_list"]
            if 0 <= idx < len(msgs):
                removed = msgs.pop(idx)
                self.db.update_task(self.task_id, messages_list=msgs, message=msgs[0] if msgs else self.state["message"])
                self.state["need_restart"] = True
                await self._reply(self._t("messages.rotation_msg_deleted", "Message #{num} deleted", num=idx + 1))
                await self._step_edit_rotation_editor()
                return
            await self._fail(self._t("messages.wrong_choice", "Wrong number"))
            return
        # Edit: "#N" or "N"
        m = re.match(r"#?(\d+)", lowered)
        if m:
            idx = int(m.group(1)) - 1
            msgs = self.state["messages_list"]
            if 0 <= idx < len(msgs):
                self.state["edit_rotation_idx"] = idx
                self.state["step"] = "rotation_edit_msg"
                preview = msgs[idx].replace("\n", " ")[:200]
                prompt = self._t("messages.rotation_edit_msg", "Editing #{num}:\n{preview}\nSend new text:") \
                    .replace("{num}", str(idx + 1)).replace("{preview}", preview)
                buttons = [[Button.text(self._t("messages.dialog_cancel", "Cancel"))]]
                await self._reply(prompt, buttons=buttons)
                return
            await self._fail(self._t("messages.wrong_choice", "Wrong number"))
            return
        await self._fail(self._t("messages.wrong_choice", "Wrong choice"))

    async def _handle_rotation_edit_msg(self, text: str):
        # No ">>" needed — the first message is the new text.
        idx = self.state.get("edit_rotation_idx")
        if idx is not None and 0 <= idx < len(self.state["messages_list"]):
            self.state["messages_list"][idx] = text
            self.db.update_task(self.task_id, messages_list=self.state["messages_list"], message=self.state["messages_list"][0])
            self.db.update_message_index(self.task_id, 0)
            self.state["need_restart"] = True
            await self._reply(self._t("messages.rotation_msg_updated", "Message #{num} updated", num=idx + 1))
        await self._step_edit_rotation_editor()
