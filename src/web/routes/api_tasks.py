#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Web API routes for task CRUD and execution control."""

import asyncio
import json
from aiohttp import web

from src.services.logger import log
from src.services.translator import lang
from src.web.plugin_routes import invalidate_web_caches


class TaskApiRoutes:
    """Handles REST API calls for task management."""

    def __init__(self, bot):
        self.bot = bot

    def _invalidate_pages(self) -> None:
        """Drops cached dashboard/task pages after a task mutation."""
        invalidate_web_caches(self.bot)

    def _ensure_bot(self):
        if not self.bot:
            raise web.HTTPInternalServerError(text=json.dumps({"error": lang.get("web.errors.bot_not_connected", "Bot not connected")}), content_type="application/json")

    def collect(self):
        """Serializes every task. Shared by the JSON API and the initial page render."""
        self._ensure_bot()
        tasks = self.bot.db.get_all()
        result = []

        running_tasks = getattr(self.bot.task_service, "running", {}) if hasattr(self.bot, "task_service") else getattr(self.bot, "running", {})
        scheduler = getattr(self.bot, "scheduler_service", None)

        for t in tasks:
            tid = t[0]
            chat = t[1]
            msg = t[2]
            media_path = t[3]
            media_type = t[4]
            cfg = t[5]
            active = t[6]
            total = t[7]
            last = t[8]
            is_once = t[9]
            once_time = t[10]
            msg_list = t[12] if len(t) > 12 else None
            chats = t[13] if len(t) > 13 else None

            interval_str = scheduler.format_interval(cfg, is_once) if scheduler else "60s"

            valid_chats = []
            if chats:
                if isinstance(chats, list):
                    valid_chats = chats
                elif isinstance(chats, str):
                    try:
                        parsed = json.loads(chats)
                        valid_chats = parsed if isinstance(parsed, list) else [parsed]
                    except Exception:
                        valid_chats = [chats]
            if not valid_chats:
                valid_chats = [chat]

            result.append({
                "id": tid,
                "chat": chat,
                "chats": valid_chats,
                "message": msg[:100] if msg else "",
                "media_path": media_path,
                "media_type": media_type,
                "interval": interval_str,
                "interval_config": cfg,
                "active": bool(active),
                "running": tid in running_tasks,
                "total_sent": total,
                "last_sent": last,
                "is_once": bool(is_once),
                "once_time": once_time,
                "rotation_count": len(msg_list) if msg_list else 0,
            })

        result.sort(key=lambda x: x["id"], reverse=True)
        return result

    async def get_all(self, request):
        return web.json_response({"tasks": self.collect()})

    async def get_one(self, request):
        self._ensure_bot()
        try:
            tid = int(request.match_info["id"])
        except ValueError:
            return web.json_response({"error": lang.get("web.errors.invalid_id", "Invalid ID")}, status=400)

        task = self.bot.db.get_one(tid)
        if not task:
            return web.json_response({"error": lang.get("web.errors.task_not_found", "Task not found")}, status=404)

        chats = task[11] if len(task) > 11 else None
        valid_chats = []
        if chats:
            if isinstance(chats, list):
                valid_chats = chats
            elif isinstance(chats, str):
                try:
                    parsed = json.loads(chats)
                    valid_chats = parsed if isinstance(parsed, list) else [parsed]
                except Exception:
                    valid_chats = [chats]

        return web.json_response({
            "id": task[0],
            "chat": task[1],
            "message": task[2],
            "media_path": task[3],
            "media_type": task[4],
            "interval_config": task[5],
            "active": bool(task[6]),
            "is_once": bool(task[7]),
            "once_time": task[8],
            "messages_list": task[10] if len(task) > 10 else [],
            "chats_list": valid_chats,
        })

    async def create(self, request):
        self._ensure_bot()
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": lang.get("web.errors.invalid_json", "Invalid JSON")}, status=400)

        chats = data.get("chats", [])
        if not chats:
            chat = (data.get("chat") or "").strip()
            if chat:
                chats = [chat]

        if not chats:
            return web.json_response({"error": lang.get("web.errors.chat_required", "Chat is required")}, status=400)

        message = data.get("message", "")
        media_path = data.get("media_path") or None
        media_type = data.get("media_type") or None
        messages_list = data.get("messages_list") or None
        is_once = data.get("is_once", 0)
        once_time_str = data.get("once_time")

        if not message and not media_path and not messages_list:
            return web.json_response({"error": lang.get("web.errors.content_required", "Text, media or rotation required")}, status=400)

        interval_str = data.get("interval", "60")
        scheduler = getattr(self.bot, "scheduler_service", None)
        cfg = scheduler.parse_interval(interval_str) if scheduler else {"type": "interval", "seconds": 60}
        if not cfg:
            return web.json_response({"error": lang.get("web.errors.invalid_interval", "Invalid interval")}, status=400)

        once_time = None
        if is_once and scheduler:
            once_time = scheduler.parse_once(once_time_str)
            if not once_time:
                return web.json_response({"error": lang.get("web.errors.invalid_once_time", "Invalid once time format")}, status=400)

        chats_json = json.dumps(chats) if len(chats) > 1 else None
        main_chat = chats[0]
        main_message = message or (messages_list[0] if messages_list else "")

        tid = self.bot.db.add(
            chat_id=main_chat,
            message=main_message,
            media_path=media_path,
            media_type=media_type,
            interval_config=cfg,
            is_once=is_once,
            once_time=once_time.strftime("%Y-%m-%d %H:%M:%S") if once_time else None,
            messages_list=messages_list,
            chats_list=chats_json,
        )

        log.info(f"Task #{tid} created via web for {len(chats)} chat(s)")
        self._invalidate_pages()
        return web.json_response({"id": tid, "success": True})

    async def update(self, request):
        self._ensure_bot()
        try:
            tid = int(request.match_info["id"])
            data = await request.json()
        except Exception:
            return web.json_response({"error": lang.get("web.errors.invalid_request", "Invalid request")}, status=400)

        task = self.bot.db.get_one(tid)
        if not task:
            return web.json_response({"error": lang.get("web.errors.task_not_found", "Task not found")}, status=404)

        chats = data.get("chats", [])
        if chats:
            self.bot.db.update_task(tid, chat_id=chats[0], chats_list=chats if len(chats) > 1 else None)

        if "message" in data:
            self.bot.db.update_task(tid, message=data["message"])
        if "media_path" in data:
            self.bot.db.update_task(tid, media_path=data["media_path"] or "")
        if "media_type" in data:
            self.bot.db.update_task(tid, media_type=data["media_type"] or "")
        if "messages_list" in data:
            self.bot.db.update_task(tid, messages_list=data["messages_list"] or None)

        scheduler = getattr(self.bot, "scheduler_service", None)
        if "is_once" in data:
            is_once = data["is_once"]
            once_time = scheduler.parse_once(data.get("once_time", "")) if (is_once and scheduler) else None
            self.bot.db.update_task(tid, is_once=1 if is_once else 0, once_time=once_time)

        if "interval" in data and scheduler:
            cfg = scheduler.parse_interval(data["interval"])
            if cfg:
                self.bot.db.update_task(tid, interval_config=cfg)

        task_service = getattr(self.bot, "task_service", None)
        if task_service and tid in task_service.running:
            await task_service.stop_task(tid)
            await asyncio.sleep(0.5)
            await task_service.start_task(tid)

        self._invalidate_pages()
        return web.json_response({"success": True})

    async def delete(self, request):
        self._ensure_bot()
        try:
            tid = int(request.match_info["id"])
        except ValueError:
            return web.json_response({"error": lang.get("web.errors.invalid_id", "Invalid ID")}, status=400)

        task_service = getattr(self.bot, "task_service", None)
        if task_service:
            await task_service.stop_task(tid)
        self.bot.db.delete(tid)
        self._invalidate_pages()
        return web.json_response({"success": True})

    async def start(self, request):
        self._ensure_bot()
        try:
            tid = int(request.match_info["id"])
        except ValueError:
            return web.json_response({"error": lang.get("web.errors.invalid_id", "Invalid ID")}, status=400)

        task_service = getattr(self.bot, "task_service", None)
        if task_service:
            await task_service.start_task(tid)
        self._invalidate_pages()
        return web.json_response({"success": True})

    async def stop(self, request):
        self._ensure_bot()
        try:
            tid = int(request.match_info["id"])
        except ValueError:
            return web.json_response({"error": lang.get("web.errors.invalid_id", "Invalid ID")}, status=400)

        task_service = getattr(self.bot, "task_service", None)
        if task_service:
            await task_service.stop_task(tid)
        return web.json_response({"success": True})

    async def activate(self, request):
        self._ensure_bot()
        try:
            tid = int(request.match_info["id"])
        except ValueError:
            return web.json_response({"error": lang.get("web.errors.invalid_id", "Invalid ID")}, status=400)

        self.bot.db.set_active(tid, True)
        self._invalidate_pages()
        return web.json_response({"success": True})

    async def deactivate(self, request):
        self._ensure_bot()
        try:
            tid = int(request.match_info["id"])
        except ValueError:
            return web.json_response({"error": lang.get("web.errors.invalid_id", "Invalid ID")}, status=400)

        task_service = getattr(self.bot, "task_service", None)
        if task_service:
            await task_service.stop_task(tid)
        self.bot.db.set_active(tid, False)
        self._invalidate_pages()
        return web.json_response({"success": True})

    async def stop_all(self, request):
        self._ensure_bot()
        task_service = getattr(self.bot, "task_service", None)
        count = len(task_service.running) if task_service else 0
        if task_service:
            await task_service.stop_all()
        self._invalidate_pages()
        return web.json_response({"success": True, "stopped": count})

    async def deactivate_all(self, request):
        self._ensure_bot()
        tasks = self.bot.db.get_all()
        count = 0
        task_service = getattr(self.bot, "task_service", None)
        for t in tasks:
            tid = t[0]
            if t[6]:
                if task_service:
                    await task_service.stop_task(tid)
                self.bot.db.set_active(tid, False)
                count += 1
        self._invalidate_pages()
        return web.json_response({"success": True, "deactivated": count})

    def register(self, app: web.Application):
        """Registers task API routes."""
        app.router.add_get("/api/tasks", self.get_all)
        app.router.add_post("/api/tasks", self.create)
        app.router.add_post("/api/tasks/stop-all", self.stop_all)
        app.router.add_post("/api/tasks/deactivate-all", self.deactivate_all)

        app.router.add_get("/api/tasks/{id}", self.get_one)
        app.router.add_put("/api/tasks/{id}", self.update)
        app.router.add_delete("/api/tasks/{id}", self.delete)
        app.router.add_post("/api/tasks/{id}/start", self.start)
        app.router.add_post("/api/tasks/{id}/stop", self.stop)
        app.router.add_post("/api/tasks/{id}/activate", self.activate)
        app.router.add_post("/api/tasks/{id}/deactivate", self.deactivate)
