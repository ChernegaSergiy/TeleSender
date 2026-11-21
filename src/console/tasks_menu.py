#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Console task management workflows."""

import asyncio
import json
import os
import shutil
from typing import List, Optional

from src.console.input_helper import InputHelper
from src.services.database_service import Colors, print_color
from src.services.logger import log
from src.services.translator import lang


class TasksMenu:
    """Handles terminal UI operations on tasks."""

    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db
        self.task_service = getattr(bot, "task_service", None)
        self.scheduler = getattr(bot, "scheduler_service", None)

    @staticmethod
    def _media_label(media_type):
        keys = {
            "photo": "media.photo",
            "video": "media.video",
            "document": "media.document",
            "audio": "media.audio",
            "sticker": "media.sticker",
            "gif": "media.gif",
            "voice": "media.voice",
            "video_note": "media.video_note",
        }
        if not media_type:
            return lang.get("messages.media_file", "file")
        return lang.get(keys.get(media_type, ""), media_type)

    @staticmethod
    def _media_type_map():
        return {
            "photo":      ("photo",      "messages.photo_path",      "Photo path: "),
            "фото":       ("photo",      "messages.photo_path",      "Photo path: "),
            "ф":          ("photo",      "messages.photo_path",      "Photo path: "),
            "p":          ("photo",      "messages.photo_path",      "Photo path: "),
            "video":      ("video",      "messages.video_path",      "Video path: "),
            "відео":      ("video",      "messages.video_path",      "Video path: "),
            "в":          ("video",      "messages.video_path",      "Video path: "),
            "v":          ("video",      "messages.video_path",      "Video path: "),
            "document":   ("document",   "messages.document_path",   "Document path: "),
            "doc":        ("document",   "messages.document_path",   "Document path: "),
            "документ":   ("document",   "messages.document_path",   "Document path: "),
            "д":          ("document",   "messages.document_path",   "Document path: "),
            "file":       ("document",   "messages.document_path",   "File path: "),
            "файл":       ("document",   "messages.document_path",   "File path: "),
            "audio":      ("audio",      "messages.audio_path",      "Audio path: "),
            "аудіо":      ("audio",      "messages.audio_path",      "Audio path: "),
            "а":          ("audio",      "messages.audio_path",      "Audio path: "),
            "au":         ("audio",      "messages.audio_path",      "Audio path: "),
            "sticker":    ("sticker",    "messages.sticker_path",    "Sticker path (.webp): "),
            "стікер":     ("sticker",    "messages.sticker_path",    "Sticker path (.webp): "),
            "с":          ("sticker",    "messages.sticker_path",    "Sticker path (.webp): "),
            "st":         ("sticker",    "messages.sticker_path",    "Sticker path (.webp): "),
            "gif":        ("gif",        "messages.gif_path",        "GIF path: "),
            "гіф":        ("gif",        "messages.gif_path",        "GIF path: "),
            "voice":      ("voice",      "messages.voice_path",      "Voice path (.ogg): "),
            "голосове":   ("voice",      "messages.voice_path",      "Voice path (.ogg): "),
            "video_note": ("video_note", "messages.video_note_path", "Video note path (.mp4): "),
            "vn":         ("video_note", "messages.video_note_path", "Video note path (.mp4): "),
            "кружок":     ("video_note", "messages.video_note_path", "Video note path (.mp4): "),
        }

    async def _console_media_type_menu(self, task, tid):
        """Shows media type list and asks for a path.

        When called from edit_task with a valid task/tid, updates the DB
        directly. When called from add_task with task=None, returns
        (media_path, media_type) for the caller to use.
        """
        print_color(f"\n{lang.get('messages.media_types', 'Media types:')}", Colors.YELLOW)
        print(lang.get('messages.media_photo', '• photo - images'))
        print(lang.get('messages.media_video', '• video - video files'))
        print(lang.get('messages.media_document', '• document / file - files (PDF, DOC, TXT...)'))
        print(lang.get('messages.media_audio', '• audio - music, podcasts'))
        print(lang.get('messages.media_sticker', '• sticker - Telegram stickers (.webp)'))
        print(lang.get('messages.media_gif', '• gif - GIF animations'))
        print(lang.get('messages.media_voice', '• voice - voice messages (.ogg)'))
        print(lang.get('messages.media_video_note', '• video_note - round video messages (.mp4)'))
        has_media = (await InputHelper.get_input(lang.get("messages.media_enter_type", "Media type: "))).strip().lower()
        media_type_map = self._media_type_map()
        if has_media in media_type_map:
            m_type, prompt_key, prompt_def = media_type_map[has_media]
            new_media_path = (await InputHelper.get_input(lang.get(prompt_key, prompt_def))).strip()
            if new_media_path and not os.path.exists(new_media_path):
                print_color(lang.get("messages.file_not_found", "File not found: {path}").replace("{path}", new_media_path), Colors.RED)
                if task is None:
                    return None, None
                return
            if task is not None and tid is not None:
                self.db.update_task(tid, media_path=new_media_path, media_type=m_type)
                print_color(lang.get("messages.media_updated", "Media updated!"), Colors.GREEN)
                return new_media_path, m_type
            return new_media_path, m_type
        print_color(lang.get("messages.wrong_choice", "Wrong choice"), Colors.RED)
        if task is None:
            return None, None

    async def add_task(self):
        """Interactive workflow to add a new task."""
        print_color(f"\n--- {lang.get('messages.new_task', 'New Task')} ---", Colors.CYAN)

        chats_input = await InputHelper.get_input(lang.get("messages.chat_prompt", "Chats (comma-separated): "))
        chats = [c.strip() for c in chats_input.split(",") if c.strip()]
        if not chats:
            print_color(lang.get("messages.chat_empty", "Chat cannot be empty"), Colors.RED)
            return

        chats_str = ", ".join(chats)
        chats_added_msg = (
            lang.get("messages.chats_added", "Added {count} chat(s): {chats}")
            .replace("{count}", str(len(chats)))
            .replace("{chats}", chats_str)
            .strip()
        )
        print_color(chats_added_msg, Colors.GREEN)

        media_path = None
        media_type = None
        add_media = (await InputHelper.get_input(lang.get("messages.add_media_prompt", "Add media? (y/n): "))).strip().lower()
        if add_media in ["y", "yes", "1", "д", "да", "так"]:
            media_path, media_type = await self._console_media_type_menu(None, None)
            if media_path is None and media_type is None:
                return
        else:
            media_path = None
            media_type = None

        is_rotation = (await InputHelper.get_input(lang.get("messages.enter_rotation", "Add rotation messages? (y/n): "))).strip().lower()
        messages_list = None
        msg = None

        if is_rotation == "y":
            method = await InputHelper.get_input(lang.get("messages.rotation_method", "1 - Console, 2 - Editor (1/2): "))
            if method == "1":
                messages_list = await InputHelper.get_rotation_console()
            else:
                messages_list = await InputHelper.get_rotation_editor()

            if not messages_list:
                print_color(lang.get("messages.no_messages", "No messages entered"), Colors.RED)
                return
            msg = messages_list[0]
        else:
            if media_path:
                method = await InputHelper.get_input(lang.get("messages.text_method_skip", "Text input: 1 - Console, 2 - Editor, 3 - Skip (1/2/3): "))
                if method == "1":
                    msg = await InputHelper.get_multiline_simple()
                elif method == "2":
                    msg = await InputHelper.get_multiline_editor()
                else:
                    msg = ""
            else:
                method = await InputHelper.get_input(lang.get("messages.text_method", "Text input: 1 - Console, 2 - Editor (1/2): "))
                msg = await InputHelper.get_multiline_simple() if method == "1" else await InputHelper.get_multiline_editor()
                if not msg:
                    print_color(lang.get("messages.text_empty_error", "Text cannot be empty without media"), Colors.RED)
                    return

        is_once = 0
        once_time = None
        if not messages_list:
            once_input = (await InputHelper.get_input(lang.get("messages.enter_once", "One-time task? (date time / +30m / n): "))).strip().lower()
            if once_input and once_input != "n":
                once_time = self.scheduler.parse_once(once_input) if self.scheduler else None
                if once_time:
                    is_once = 1
                else:
                    print_color(lang.get("messages.wrong_format", "Invalid format"), Colors.RED)
                    return

        cfg = {"type": "interval", "seconds": 60}
        if is_once == 0:
            print_color(f"\n{lang.get('messages.interval_formats', 'Interval formats:')}", Colors.YELLOW)
            print(lang.get('messages.interval_formats_list', '• 30, 5m, 2h, 1d | 25m10s | 60-120 | 14:10@1,3,5'))
            int_input = await InputHelper.get_input(lang.get("messages.interval_enter", "Interval (e.g. 5m, 1h, 10-30, 14:30@1,3,5): "))
            parsed_cfg = self.scheduler.parse_interval(int_input) if self.scheduler else None
            if not parsed_cfg:
                print_color(lang.get("messages.wrong_format", "Invalid format"), Colors.RED)
                return
            cfg = parsed_cfg

        tid = self.db.add(
            chat_id=chats[0],
            message=msg or "",
            media_path=media_path,
            media_type=media_type,
            interval_config=cfg,
            is_once=is_once,
            once_time=once_time.strftime("%Y-%m-%d %H:%M:%S") if once_time else None,
            messages_list=messages_list,
            chats_list=chats,
        )

        log.info(f"Task #{tid} created from console for {len(chats)} chat(s)")
        task_added_msg = lang.get("messages.task_added", "Task #{tid} added for {count} chat(s)").replace("{tid}", str(tid)).replace("{count}", str(len(chats)))
        print_color(task_added_msg, Colors.GREEN)

        run_now = (await InputHelper.get_input(lang.get("messages.run_now", "Start now? (y/n): "))).strip().lower()
        if run_now == "y" or is_once == 1:
            if self.task_service:
                self.db.update_message_index(tid, 0)
                await self.task_service.start_task(tid)

    async def list_tasks(self):
        """Displays formatted list of all scheduled tasks."""
        tasks = self.db.get_all()
        if not tasks:
            print_color(lang.get("messages.no_tasks", "No tasks found"), Colors.YELLOW)
            return

        term_width = min(shutil.get_terminal_size().columns - 2, 72)
        sep_thick = "═" * term_width
        sep_thin  = "─" * term_width

        chat_label = lang.get("task.chat", "Chat")
        chats_label = lang.get("task.chats", "Chats")
        text_label = lang.get("task.text", "Text")
        rotation_label = lang.get("task.rotation", "Rotation")
        media_label = lang.get("task.media", "Media")
        empty_label = lang.get("messages.empty_text", "empty").strip("()")
        once_label = lang.get("task.once", "Once")

        running_tasks = self.task_service.running if self.task_service else {}
        print_color(f"\n{sep_thick}", Colors.CYAN)

        for t in tasks:
            tid       = t[0]
            chat      = t[1]
            msg       = t[2]
            media_path= t[3]
            media_type= t[4]
            cfg       = t[5]
            active    = t[6]
            total     = t[7]
            last_sent = t[8]
            is_once   = t[9]  if len(t) > 9  else 0
            once_time = t[10] if len(t) > 10 else None
            msgs_list = t[12] if len(t) > 12 else []
            chats_lst = t[13] if len(t) > 13 else []

            status  = "🟢" if active else "🔴"
            running = "▶" if tid in running_tasks else "Ⅱ"

            # Primary chat display
            if chats_lst and len(chats_lst) > 1:
                chat_prefix = f"{chats_label}: {chats_lst[0]} (+{len(chats_lst)-1})"
            else:
                primary = (chats_lst[0] if chats_lst else chat) or chat
                if not isinstance(primary, str):
                    primary = str(primary)
                chat_prefix = f"{chat_label}: {primary}"

            # Media tag
            media_tag = f"  [{self._media_label(media_type)}]" if media_path and media_type else ""

            # Interval string (no '@')
            if is_once:
                int_str = f"{once_label}: {once_time}" if once_time else once_label
            else:
                int_str = self.scheduler.format_interval(cfg, 0) if self.scheduler else str(cfg)

            # Text / Rotation preview
            if msgs_list:
                clean = msgs_list[0][:45].replace("\n", " ")
                msg_preview = f"{text_label} ({rotation_label} {len(msgs_list)}): {clean}"
            elif msg and msg.strip():
                clean = msg[:50].replace("\n", " ")
                msg_preview = f"{text_label}: {clean}"
            elif media_path:
                msg_preview = f"{media_label}: {self._media_label(media_type)}"
            else:
                msg_preview = f"{text_label}: ({empty_label})"

            # Last sent
            last_str = last_sent if last_sent else "—"

            print_color(f"{status} {running} #{tid} | {chat_prefix}{media_tag}", Colors.YELLOW)
            print_color(f"{msg_preview}", Colors.RESET)
            print(f"⏱ {int_str}   ▤ {total}   ◷ {last_str}")
            print_color(sep_thin, Colors.CYAN)

        total_count = len(tasks)
        running_count = len([t for t in tasks if t[0] in running_tasks])
        active_count  = len([t for t in tasks if t[6]])
        print_color(
            lang.get(
                "messages.tasks_summary",
                "Total: {total}  |  🟢 Active: {active}  |  ▶ Running: {running}",
            ).format(total=total_count, active=active_count, running=running_count),
            Colors.MAGENTA,
        )
        print_color(sep_thick, Colors.CYAN)

    async def start_task(self):
        tid_str = await InputHelper.get_input(lang.get("messages.enter_task_id", "Task ID: "))
        try:
            tid = int(tid_str.strip())
            if self.task_service:
                await self.task_service.start_task(tid)
            print_color(f"{lang.get('commands.start_task', 'Task #{tid} started').replace('{tid}', str(tid))}", Colors.GREEN)
        except Exception:
            print_color(lang.get("messages.wrong_format", "Invalid format"), Colors.RED)

    async def stop_task(self):
        tid_str = await InputHelper.get_input(lang.get("messages.enter_task_id", "Task ID: "))
        try:
            tid = int(tid_str.strip())
            if self.task_service:
                await self.task_service.stop_task(tid)
            print_color(f"{lang.get('commands.stop_task', 'Task #{tid} stopped').replace('{tid}', str(tid))}", Colors.GREEN)
        except Exception:
            print_color(lang.get("messages.wrong_format", "Invalid format"), Colors.RED)

    async def activate_task(self):
        tid_str = await InputHelper.get_input(lang.get("messages.enter_task_id", "Task ID: "))
        try:
            tid = int(tid_str.strip())
            self.db.set_active(tid, True)
            log.info(f"Task #{tid} activated from console")
            print_color(f"{lang.get('messages.task_activated', 'Task #{tid} activated').replace('{tid}', str(tid))}", Colors.GREEN)
        except Exception:
            print_color(lang.get("messages.wrong_format", "Invalid format"), Colors.RED)

    async def deactivate_task(self):
        tid_str = await InputHelper.get_input(lang.get("messages.enter_task_id", "Task ID: "))
        try:
            tid = int(tid_str.strip())
            if self.task_service:
                await self.task_service.stop_task(tid)
            self.db.set_active(tid, False)
            log.info(f"Task #{tid} deactivated from console")
            print_color(f"{lang.get('messages.task_deactivated', 'Task #{tid} deactivated').replace('{tid}', str(tid))}", Colors.GREEN)
        except Exception:
            print_color(lang.get("messages.wrong_format", "Invalid format"), Colors.RED)

    async def delete_task(self):
        tid_str = await InputHelper.get_input(lang.get("messages.enter_task_id", "Task ID: "))
        try:
            tid = int(tid_str.strip())
            if self.task_service:
                await self.task_service.stop_task(tid)
            self.db.delete(tid)
            log.info(f"Task #{tid} deleted from console")
            print_color(f"{lang.get('messages.task_deleted', 'Task #{tid} deleted').replace('{tid}', str(tid))}", Colors.GREEN)
        except Exception:
            print_color(lang.get("messages.wrong_format", "Invalid format"), Colors.RED)

    async def show_task_detail(self):
        """Displays detailed task inspection formatted identically to console.py."""
        tid_str = await InputHelper.get_input(lang.get("messages.enter_task_id", "Task ID: "))
        try:
            tid = int(tid_str.strip())
        except ValueError:
            print_color(lang.get("messages.wrong_format", "Invalid format"), Colors.RED)
            return

        task = self.db.get_full(tid)
        if not task:
            print_color(lang.get("messages.task_not_found", "Task #{tid} not found").replace("{tid}", str(tid)), Colors.RED)
            return

        term_width = min(shutil.get_terminal_size().columns - 2, 72)
        line = "═" * term_width

        print_color(f"\n{line}", Colors.CYAN)
        print_color(f"{lang.get('task.detail', 'ЗАВДАННЯ')} #{tid}", Colors.MAGENTA)
        print_color(f"{line}", Colors.CYAN)

        # Chats
        chats = task["chats_list"]
        if chats and len(chats) > 1:
            print(f"{lang.get('task.chats', 'Чати')} ({len(chats)}):")
            for c in chats:
                print(f"• {c}")
        elif chats and len(chats) == 1:
            print(f"{lang.get('task.chat', 'Чат')}: {chats[0]}")
        else:
            print(f"{lang.get('task.chat', 'Чат')}: {task['chat_id']}")

        # Message
        if task["message"]:
            print(f"{lang.get('task.text', 'Текст')}: {task['message']}")
        else:
            print(f"{lang.get('task.text', 'Текст')}: ({lang.get('messages.empty_text', 'порожньо')})")

        # Media
        if task["media_path"]:
            media_name = self._media_label(task["media_type"])
            print(f"{lang.get('task.media', 'Медіа')}: {os.path.basename(task['media_path'])} ({media_name})")
            print(f"{lang.get('task.path', 'Шлях')}: {task['media_path']}")

        # Rotation
        if task["messages_list"]:
            print(f"{lang.get('task.rotation', 'Ротація')}: {len(task['messages_list'])} {lang.get('task.messages', 'повідомлень')}")
            for i, m in enumerate(task["messages_list"]):
                preview = m.replace("\n", "↵ ")[:50]
                print(f"#{i+1}: {preview}{'...' if len(m) > 50 else ''}")

        # Interval / Once
        if task["is_once"]:
            print(f"⏱ {lang.get('task.once', 'Разове')}: {task['once_time'] or '—'}")
        else:
            interval_display = self.scheduler.format_interval(task["interval_config"], 0) if self.scheduler else str(task["interval_config"])
            print(f"⏱ {lang.get('task.interval', 'Інтервал')}: {interval_display}")

        # Status
        active = task["is_active"]
        status_text = lang.get("task.active", "Активне") if active else lang.get("task.inactive", "Неактивне")
        status_icon = "🟢" if active else "🔴"
        running_tasks = self.task_service.running if self.task_service else {}
        running_icon = "▶" if tid in running_tasks else "Ⅱ"
        print(f"{status_icon} {lang.get('task.status', 'Статус')}: {status_text} {running_icon}")

        # Counters & timestamps
        print(f"▤ {lang.get('task.total_sent', 'Всього відправок')}: {task['total_sent']}")
        if task["last_sent"]:
            print(f"◷ {lang.get('task.last_sent', 'Остання відправка')}: {task['last_sent']}")
        if task["created_at"]:
            print(f"{lang.get('task.created', 'Створено')}: {task['created_at']}")
        else:
            print(f"{lang.get('task.created', 'Створено')}: {lang.get('messages.unknown', 'Невідомо')}")

        print_color(f"{line}", Colors.CYAN)

    async def edit_task(self):
        """Full interactive task editor ported from legacy console.py."""
        tid_str = await InputHelper.get_input(lang.get("messages.enter_task_id_edit", "Task ID to edit: "))
        try:
            tid = int(tid_str.strip())
        except ValueError:
            print_color(lang.get("messages.wrong_format", "Invalid format"), Colors.RED)
            return

        task = self.db.get_one(tid)
        if not task:
            print_color(lang.get("messages.task_not_found", "Task #{tid} not found").replace("{tid}", str(tid)), Colors.RED)
            return

        need_restart = False

        # --- Chats ---
        current_chats = task[11] if task[11] else [task[1]]
        chats_display = ", ".join(current_chats[:3]) if isinstance(current_chats, list) else str(current_chats)
        print_color(f"\n{lang.get('messages.edit_chat', 'Current chat: {chat}').replace('{chat}', chats_display)}", Colors.CYAN)
        print_color(lang.get("messages.edit_chat_hint", "For multiple chats separate with comma: @chat1,@chat2,123456"), Colors.YELLOW)
        new_chats_input = (await InputHelper.get_input(lang.get("messages.edit_chat_new", "New chat(s) (Enter to keep): "))).strip()

        if new_chats_input:
            new_chats = [c.strip() for c in new_chats_input.split(",") if c.strip()]
            if new_chats:
                self.db.update_task(tid, chat_id=new_chats[0], chats_list=new_chats)
                need_restart = True
                updated_msg = lang.get("messages.edit_chat_updated", "Chats updated! {count} chat(s): {chats}") \
                    .replace("{count}", str(len(new_chats))).replace("{chats}", ", ".join(new_chats[:3]))
                print_color(updated_msg, Colors.GREEN)
                task = self.db.get_one(tid)

        # --- Text (no rotation) ---
        current_messages: List[str] = task[10] if task[10] else []

        if not current_messages:
            print_color(f"\n{lang.get('messages.edit_text', 'Current text:')}", Colors.CYAN)
            print("─" * 40)
            print(task[2][:500] if task[2] else f"({lang.get('messages.empty_text', 'empty')})")
            print("─" * 40)

            change_text = (await InputHelper.get_input(lang.get("messages.edit_text_change", "Change text? (y/n): "))).strip().lower()
            if change_text == "y":
                print_color(f"\n{lang.get('messages.edit_text_method', 'How to enter new text?')}", Colors.CYAN)
                print(lang.get('messages.edit_text_method_console', '1 - Console (>> to finish)'))
                print(lang.get('messages.edit_text_method_editor', '2 - Open editor'))
                if task[3]:
                    print(lang.get('messages.edit_text_method_delete', '3 - Delete text (leave empty)'))

                prompt_key = "messages.select_1_2_3" if task[3] else "messages.select_1_2"
                text_method = (await InputHelper.get_input(lang.get(prompt_key, "Choice: "))).strip()

                if text_method == "1":
                    new_msg = await InputHelper.get_multiline_simple()
                    if new_msg or task[3]:
                        self.db.update_task(tid, message=new_msg)
                        need_restart = True
                        print_color(lang.get("messages.text_updated", "Text updated!"), Colors.GREEN)
                        task = self.db.get_one(tid)
                    else:
                        print_color(lang.get("messages.text_empty_error", "Cannot leave text empty when there is no media!"), Colors.RED)
                elif text_method == "2":
                    new_msg = await InputHelper.get_multiline_editor(task[2] or "")
                    if new_msg or task[3]:
                        self.db.update_task(tid, message=new_msg)
                        need_restart = True
                        print_color(lang.get("messages.text_updated", "Text updated!"), Colors.GREEN)
                        task = self.db.get_one(tid)
                    else:
                        print_color(lang.get("messages.text_empty_error", "Cannot leave text empty when there is no media!"), Colors.RED)
                elif text_method == "3" and task[3]:
                    self.db.update_task(tid, message="")
                    need_restart = True
                    print_color(lang.get("messages.text_deleted", "Text deleted!"), Colors.GREEN)
                    task = self.db.get_one(tid)

        # --- Rotation messages ---
        current_messages = task[10] if task[10] else []

        if current_messages:
            print_color(f"\n{lang.get('task.rotation', 'Current rotation')}: {len(current_messages)} {lang.get('task.messages', 'messages')}", Colors.CYAN)
            for i, m in enumerate(current_messages[:3]):
                preview = m.replace("\n", " ")[:40]
                print_color(f"#{i+1}: {preview}{'...' if len(m) > 40 else ''}", Colors.YELLOW)
            if len(current_messages) > 3:
                print_color(f"... {lang.get('messages.and_more', 'and {count} more').replace('{count}', str(len(current_messages)-3))}", Colors.YELLOW)

            change_rotation = (await InputHelper.get_input(lang.get("messages.rotation_change", "Change rotation messages? (y/n): "))).strip().lower()
            if change_rotation == "y":
                print_color(f"\n{lang.get('messages.rotation_new', 'Enter new rotation messages')}", Colors.CYAN)
                print(lang.get('messages.rotation_console', '1 - Console (=== separator)'))
                print(lang.get('messages.rotation_editor', '2 - Open editor'))
                print(lang.get('messages.rotation_delete', '3 - Delete rotation (keep first message only)'))

                rotation_choice = (await InputHelper.get_input(lang.get("messages.select_1_2_3", "Choice (1/2/3): "))).strip()

                if rotation_choice == "3":
                    first_msg = current_messages[0] if current_messages else (task[2] or "")
                    self.db.update_task(tid, messages_list=None, message=first_msg)
                    self.db.update_message_index(tid, 0)
                    print_color(lang.get("messages.rotation_deleted", "Rotation deleted! Kept: {msg}").replace("{msg}", first_msg[:50]), Colors.GREEN)
                    need_restart = True
                    task = self.db.get_one(tid)
                    current_messages = []
                elif rotation_choice in ("1", "2"):
                    if rotation_choice == "1":
                        new_messages = await InputHelper.get_rotation_console()
                    else:
                        new_messages = await InputHelper.get_rotation_editor(current_messages)

                    if new_messages and len(new_messages) > 1:
                        self.db.update_task(tid, messages_list=new_messages, message=new_messages[0])
                        self.db.update_message_index(tid, 0)
                        updated_rot = lang.get("messages.rotation_updated", "Rotation updated! {count} messages").replace("{count}", str(len(new_messages)))
                        print_color(updated_rot, Colors.GREEN)
                        for i, m in enumerate(new_messages[:3]):
                            preview = m.replace("\n", " ")[:40]
                            print_color(f"#{i+1}: {preview}{'...' if len(m) > 40 else ''}", Colors.YELLOW)
                        if len(new_messages) > 3:
                            print_color(f"... {lang.get('messages.and_more', 'and {count} more').replace('{count}', str(len(new_messages)-3))}", Colors.YELLOW)
                        need_restart = True
                        current_messages = new_messages
                        task = self.db.get_one(tid)
                    elif new_messages and len(new_messages) == 1:
                        self.db.update_task(tid, messages_list=None, message=new_messages[0])
                        self.db.update_message_index(tid, 0)
                        print_color(lang.get("messages.rotation_one_deleted", "One message added, rotation removed"), Colors.GREEN)
                        need_restart = True
                        task = self.db.get_one(tid)
                    else:
                        print_color(lang.get("messages.no_messages", "No messages entered"), Colors.RED)

        # --- Media ---
        has_media = bool(task[3])
        if has_media:
            current_media_display = f"{os.path.basename(task[3])} ({self._media_label(task[4])})"
        else:
            current_media_display = lang.get("messages.no_media", "None")

        print_color(f"\n{lang.get('task.media', 'Current media')}: {current_media_display}", Colors.CYAN)
        media_key = "messages.media_change" if has_media else "messages.media_add"
        change_media = (await InputHelper.get_input(lang.get(media_key, "Change media? (y/n): "))).strip().lower()

        if change_media == "y":
            if has_media:
                delete_media = (await InputHelper.get_input(lang.get("messages.media_delete", "Delete media? (y/n): "))).strip().lower()
                if delete_media == "y":
                    if not (task[2] and task[2].strip()):
                        print_color(lang.get("messages.media_delete_error", "Cannot delete media: text is also empty!"), Colors.RED)
                    else:
                        self.db.update_task(tid, media_path="", media_type="")
                        print_color(lang.get("messages.media_deleted", "Media deleted!"), Colors.GREEN)
                        need_restart = True
                        task = self.db.get_one(tid)
                else:
                    await self._console_media_type_menu(task, tid)
                    task = self.db.get_one(tid)
                    need_restart = True
            else:
                # No media yet — offer to add one.
                await self._console_media_type_menu(task, tid)
                task = self.db.get_one(tid)
                need_restart = True

        # --- Interval ---
        current_interval_str = self.scheduler.format_interval(task[5]) if self.scheduler else str(task[5])
        new_interval = (await InputHelper.get_input(
            lang.get("messages.edit_interval", "New interval (current: {interval}): ").replace("{interval}", current_interval_str)
        )).strip()
        if new_interval and self.scheduler:
            cfg = self.scheduler.parse_interval(new_interval)
            if cfg:
                self.db.update_task(tid, interval_config=cfg)
                need_restart = True
            else:
                print_color(lang.get("messages.wrong_format", "Invalid format"), Colors.RED)

        # --- Restart if needed ---
        if need_restart and self.task_service:
            if tid in getattr(self.task_service, "running", {}):
                await self.task_service.stop_task(tid)
                await self.task_service.start_task(tid)

        log.info(f"Task #{tid} updated from console")
        print_color(lang.get("messages.task_updated", "Task #{tid} updated").replace("{tid}", str(tid)), Colors.GREEN)
