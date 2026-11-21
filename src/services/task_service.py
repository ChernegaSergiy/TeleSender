#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Task execution service controlling background worker routines."""

import asyncio
import json
import os
import random
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.console.terminal import clear_status, update_status, write_line
from src.services.database_service import Colors, DatabaseService
from src.services.logger import log
from src.services.scheduler_service import SchedulerService
from src.services.telegram_service import TelegramService
from src.services.translator import lang


class TaskService:
    """Manages scheduled background task loops and execution state."""

    def __init__(
        self,
        db: DatabaseService,
        telegram_service: TelegramService,
        scheduler_service: SchedulerService,
    ):
        self.db = db
        self.telegram = telegram_service
        self.scheduler = scheduler_service
        self.running: Dict[int, bool] = {}
        self._tasks: Dict[int, asyncio.Task] = {}
        self._last_send_time: Dict[int, str] = {}

    async def worker(
        self,
        tid: int,
        *args,
        **kwargs,
    ):
        """Runs a task worker and clears its console status when it exits."""
        try:
            await self._run_worker(tid, *args, **kwargs)
        finally:
            clear_status(f"task-{tid}")

    async def _run_worker(
        self,
        tid: int,
        chat_id: str,
        msg: str,
        media_path: Optional[str],
        media_type: Optional[str],
        cfg: Dict[str, Any],
        is_once: int,
        once_time: Optional[str],
        messages_list: Optional[List[str]],
        message_index: int,
        chats_list: Optional[Any] = None,
    ):
        """Worker routine executing task schedule and sending payloads."""
        target_chats = []
        if chats_list:
            if isinstance(chats_list, list):
                target_chats = chats_list
            elif isinstance(chats_list, str):
                try:
                    target_chats = json.loads(chats_list)
                    if not isinstance(target_chats, list):
                        target_chats = [target_chats]
                except Exception:
                    target_chats = [chats_list]
        else:
            target_chats = [chat_id]

        typ = cfg.get("type") if cfg else "interval"
        count = 0
        first = True
        current_idx = message_index or 0
        sent_once = False
        waiting = True

        interval_desc = self.scheduler.format_interval(cfg, is_once)
        task_label = lang.get("worker.task", "Task")
        chats_label = lang.get("messages.chats_label", "chats")
        if len(target_chats) > 1:
            chat_previews = ", ".join([str(c)[:20] for c in target_chats[:3]])
            ellipsis_str = "..." if len(target_chats) > 3 else ""
            write_line(f"{Colors.GREEN}▶ {task_label} #{tid} | {len(target_chats)} {chats_label}: {chat_previews}{ellipsis_str} ({interval_desc}){Colors.RESET}")
        else:
            write_line(f"{Colors.GREEN}▶ {task_label} #{tid} ({interval_desc}){Colors.RESET}")
        log.info(f"Task #{tid} worker started: {len(target_chats)} chat(s), {interval_desc}")

        if media_path:
            log.debug(f"Task #{tid} media: {os.path.basename(media_path)}")
        if messages_list:
            log.debug(f"Task #{tid} rotation: {len(messages_list)} messages")

        while self.running.get(tid, False):
            if is_once:
                if sent_once:
                    self.db.set_active(tid, False)
                    self.running.pop(tid, None)
                    break

                if once_time and waiting:
                    try:
                        clean_time = once_time.split(".")[0] if "." in once_time else once_time
                        once_dt = datetime.strptime(clean_time, "%Y-%m-%d %H:%M:%S")
                        now = datetime.now().replace(microsecond=0)
                        if now < once_dt:
                            wait = (once_dt - now).seconds
                            if wait > 0:
                                for i in range(wait, 0, -1):
                                    if not self.running.get(tid, False):
                                        return
                                    if i % 10 == 0 or i <= 10:
                                        wait_msg = lang.get("worker.waiting", "waiting")
                                        to_send_msg = lang.get("worker.to_send", "until send")
                                        dur_str = self.scheduler.format_seconds(i)
                                        update_status(f"task-{tid}", f"#{tid} {wait_msg} {dur_str} {to_send_msg}")
                                    await asyncio.sleep(1)
                                clear_status(f"task-{tid}")
                                continue
                    except Exception as e:
                        log.error(f"Task #{tid} once time error: {e}")
                    waiting = False

                send_idx = 0
                send_msg = msg
                if messages_list:
                    send_idx = current_idx % len(messages_list)
                    send_msg = messages_list[send_idx]

                for target in target_chats:
                    success = await self.telegram.send(target, send_msg, media_path, media_type)
                    if success:
                        self.db.update_sent(tid)
                        count += 1
                        now = datetime.now()
                        write_line(f"  #{tid} -> {target} {now.strftime('%H:%M:%S')} (#{count}) - #{send_idx+1}: {send_msg[:30]}")
                        log.info(f"Task #{tid} sent to {target} (#{count})")
                    else:
                        log.warning(f"Task #{tid} failed to send to {target}")

                if messages_list:
                    current_idx += 1

                sent_once = True
                self.db.set_active(tid, False)
                self.running.pop(tid, None)
                break

            # Continuous scheduled tasks
            if typ == "schedule_multi":
                if first:
                    target_time, _ = self.scheduler.get_next_schedule_time(cfg)
                    wait_seconds = max(1, int((target_time - datetime.now()).total_seconds()))
                    for i in range(wait_seconds, 0, -1):
                        if not self.running.get(tid, False):
                            return
                        if i % 10 == 0 or i <= 10:
                            first_in_msg = lang.get("worker.first_in", "first run in")
                            update_status(f"task-{tid}", f"#{tid} {first_in_msg} {self.scheduler.format_seconds(i)}")
                        await asyncio.sleep(1)
                    clear_status(f"task-{tid}")
                    first = False
                    continue
                else:
                    now = datetime.now()
                    sec = now.strftime("%Y-%m-%d %H:%M:%S")
                    if tid in self._last_send_time and self._last_send_time[tid] == sec:
                        await asyncio.sleep(1)
                        continue

                    send_idx = 0
                    send_msg = msg
                    if messages_list:
                        send_idx = current_idx % len(messages_list)
                        send_msg = messages_list[send_idx]

                    for target in target_chats:
                        success = await self.telegram.send(target, send_msg, media_path, media_type)
                        if success:
                            self.db.update_sent(tid)
                            count += 1
                            self._last_send_time[tid] = sec
                            now_time = datetime.now()
                            write_line(f"  #{tid} -> {target} {now_time.strftime('%H:%M:%S')} (#{count}) - #{send_idx+1}: {send_msg[:30]}")
                            log.info(f"Task #{tid} sent to {target} (#{count})")
                        else:
                            log.warning(f"Task #{tid} failed to send to {target}")

                    if messages_list:
                        current_idx += 1
                        self.db.update_message_index(tid, current_idx)

                    target_time, _ = self.scheduler.get_next_schedule_time(cfg)
                    wait_seconds = max(1, int((target_time - datetime.now()).total_seconds()))
                    for i in range(wait_seconds, 0, -1):
                        if not self.running.get(tid, False):
                            return
                        if i % 10 == 0 or i <= 10:
                            next_in_msg = lang.get("worker.next_in", "next run in")
                            update_status(f"task-{tid}", f"#{tid} {next_in_msg} {self.scheduler.format_seconds(i)}")
                        await asyncio.sleep(1)
                    clear_status(f"task-{tid}")

            elif typ == "random":
                if not first:
                    send_idx = 0
                    send_msg = msg
                    if messages_list:
                        send_idx = current_idx % len(messages_list)
                        send_msg = messages_list[send_idx]

                    for target in target_chats:
                        success = await self.telegram.send(target, send_msg, media_path, media_type)
                        if success:
                            self.db.update_sent(tid)
                            count += 1
                            now = datetime.now()
                            write_line(f"  #{tid} -> {target} {now.strftime('%H:%M:%S')} (#{count}) - #{send_idx+1}: {send_msg[:30]}")
                            log.info(f"Task #{tid} sent to {target} (#{count})")
                        else:
                            log.warning(f"Task #{tid} failed to send to {target}")

                    if messages_list:
                        current_idx += 1
                        self.db.update_message_index(tid, current_idx)

                first = False
                wait = random.randint(cfg.get("min", 5), cfg.get("max", 60))
                for i in range(wait, 0, -1):
                    if not self.running.get(tid, False):
                        return
                    if i % 5 == 0 or i <= 5:
                        next_in_msg = lang.get("worker.next_in", "next run in")
                        update_status(f"task-{tid}", f"#{tid} {next_in_msg} {self.scheduler.format_seconds(i)}")
                    await asyncio.sleep(1)
                clear_status(f"task-{tid}")

            else:
                if not first:
                    send_idx = 0
                    send_msg = msg
                    if messages_list:
                        send_idx = current_idx % len(messages_list)
                        send_msg = messages_list[send_idx]

                    for target in target_chats:
                        success = await self.telegram.send(target, send_msg, media_path, media_type)
                        if success:
                            self.db.update_sent(tid)
                            count += 1
                            now = datetime.now()
                            write_line(f"  #{tid} -> {target} {now.strftime('%H:%M:%S')} (#{count}) - #{send_idx+1}: {send_msg[:30]}")
                            log.info(f"Task #{tid} sent to {target} (#{count})")
                        else:
                            log.warning(f"Task #{tid} failed to send to {target}")

                    if messages_list:
                        current_idx += 1
                        self.db.update_message_index(tid, current_idx)

                first = False
                wait = cfg.get("seconds", 60)
                if wait > 0:
                    for i in range(wait, 0, -1):
                        if not self.running.get(tid, False):
                            return
                        if i % 5 == 0 or i <= 5:
                            in_msg = lang.get("worker.in", "in")
                            update_status(f"task-{tid}", f"#{tid} {in_msg} {self.scheduler.format_seconds(i)}")
                        await asyncio.sleep(1)
                    clear_status(f"task-{tid}")

        self.running.pop(tid, None)
        log.info(f"Task #{tid} worker stopped (sent {count})")

    async def start_task(self, tid: int) -> bool:
        """Starts a specific task execution worker.

        Always resets the rotation index to 0 so a restarted task begins
        from the first message instead of resuming mid-rotation.
        """
        if tid in self.running and self.running[tid]:
            return True

        for task in self.db.get_active():
            if task[0] == tid:
                self.running[tid] = True
                chats = None
                if len(task) > 10 and task[10]:
                    if isinstance(task[10], list):
                        chats = task[10]
                    else:
                        try:
                            chats = json.loads(task[10])
                        except Exception:
                            chats = []

                # Reset rotation index — always start from the first message.
                if task[9]:
                    try:
                        self.db.update_message_index(tid, 0)
                    except Exception:
                        pass

                log.info(f"Task #{tid} started")
                coro = self.worker(
                    tid=task[0],
                    chat_id=task[1],
                    msg=task[2],
                    media_path=task[3],
                    media_type=task[4],
                    cfg=task[5],
                    is_once=task[6],
                    once_time=task[7],
                    messages_list=task[9] if len(task) > 9 else None,
                    message_index=0,
                    chats_list=chats,
                )
                self._tasks[tid] = asyncio.ensure_future(coro)
                return True

        err_msg = lang.get("messages.task_not_found", "Task #{tid} not found").replace("{tid}", str(tid))
        log.error(err_msg)
        return False

    async def stop_task(self, tid: int):
        """Stops a running task and waits until the worker actually exits."""
        if tid in self.running:
            self.running[tid] = False
            self._last_send_time.pop(tid, None)
            task = self._tasks.pop(tid, None)
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
            self.running.pop(tid, None)
            log.info(f"Task #{tid} stopped")

    async def stop_all(self):
        """Stops all running tasks and waits until every worker exits."""
        running_ids = list(self.running.keys())
        for tid in running_ids:
            self.running[tid] = False
            self._last_send_time.pop(tid, None)
        for tid, task in list(self._tasks.items()):
            if not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        self._tasks.clear()
        self.running.clear()
        log.info(f"Stopped all tasks: {len(running_ids)}")
