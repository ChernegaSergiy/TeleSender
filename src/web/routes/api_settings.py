#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Web API routes for system metrics, bot profile, and logs."""

import asyncio
import json
import os
import time
from pathlib import Path

from aiohttp import web

from src.config.config import config
from src.services.export_import_service import ExportImportService
from src.services.logger import log
from src.services.translator import SUPPORTED_LANGUAGES, lang
from src.web import auth as web_auth
from src.web.routes.auth import get_client_hints
from src.web.two_factor import two_factor_manager, save_2fa_setting
from src.web.ws import websocket_logs
from src.web.plugin_routes import invalidate_web_caches


class SettingsApiRoutes:
    """Handles REST calls for bot status, stats, and log streams."""

    def __init__(self, bot, start_time: float):
        self.bot = bot
        self.start_time = start_time

    async def get_stats(self, request):
        if not self.bot:
            return web.json_response({"error": lang.get("web.errors.bot_not_connected", "Bot not connected")}, status=500)

        tasks = self.bot.db.get_all()
        total_sent = sum(t[7] for t in tasks)
        active = sum(1 for t in tasks if t[6])

        running_tasks = getattr(self.bot.task_service, "running", {}) if hasattr(self.bot, "task_service") else getattr(self.bot, "running", {})

        plugins_count = 0
        plugin_service = getattr(self.bot, "plugin_service", None)
        if plugin_service:
            plugins_count = len(plugin_service.get_loaded_plugins())

        uptime_seconds = int(time.time() - self.start_time)
        scheduler = getattr(self.bot, "scheduler_service", None)
        uptime_str = scheduler.format_seconds(uptime_seconds) if scheduler else f"{uptime_seconds}s"

        return web.json_response({
            "tasks_total": len(tasks),
            "tasks_active": active,
            "tasks_running": len(running_tasks),
            "total_sent": total_sent,
            "plugins_count": plugins_count,
            "last_start": getattr(self.bot, "_last_start", None) or "—",
            "uptime": uptime_str,
        })

    async def get_bot(self, request):
        if not self.bot or not self.bot.client:
            return web.json_response({"error": lang.get("web.errors.bot_not_connected", "Bot not connected")}, status=500)

        try:
            me = await self.bot.client.get_me()
            return web.json_response({
                "name": f"{me.first_name or ''} {me.last_name or ''}".strip(),
                "username": f"@{me.username}" if me.username else "—",
                "user_id": me.id,
                "phone": getattr(me, "phone", None) or "—",
                "session": "session",
                "premium": getattr(me, "premium", False),
                "bot": getattr(me, "bot", False),
            })
        except Exception as e:
            return web.json_response({"error": lang.get("web.errors.telegram_info_failed", "Failed to load Telegram info: {error}").format(error=e)}, status=500)

    def _uptime(self) -> str:
        uptime_seconds = int(time.time() - self.start_time)
        scheduler = getattr(self.bot, "scheduler_service", None)
        return scheduler.format_seconds(uptime_seconds) if scheduler else f"{uptime_seconds}s"

    @staticmethod
    def _save_language(lang_code: str):
        env_path = Path(".env")
        lines = []
        if env_path.exists():
            lines = env_path.read_text(encoding="utf-8").splitlines()

        replaced = False
        for index, line in enumerate(lines):
            if line.strip().startswith("LANGUAGE="):
                lines[index] = f"LANGUAGE={lang_code}"
                replaced = True
                break

        if not replaced:
            lines.append(f"LANGUAGE={lang_code}")

        env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    async def get_settings(self, request):
        return web.json_response({
            "language": lang.get_lang(),
            "languages": list(SUPPORTED_LANGUAGES),
            "language_names": SUPPORTED_LANGUAGES,
            "web_host": config.web_host,
            "web_port": config.web_port,
            "uptime": self._uptime() if self.bot else "—",
            "web_auth_2fa": getattr(config, "web_auth_2fa", False),
        })

    async def set_2fa(self, request):
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": lang.get("web.errors.invalid_json", "Invalid JSON")}, status=400)

        enabled = bool(data.get("enabled", False))

        # Disabling 2FA is security-sensitive: it must be confirmed from the
        # owner's Telegram account via the inline bot.
        if not enabled:
            if not config.web_auth_2fa:
                return web.json_response({"success": True, "enabled": False})

            req = two_factor_manager.create_request(
                ip=web_auth.get_client_ip(request),
                user_agent=(request.headers.get("User-Agent") or "Unknown")[:512],
                hints=get_client_hints(request),
                purpose="disable_2fa",
            )

            sent = False
            inline = getattr(self.bot, "inline", None) if self.bot else None
            if inline:
                try:
                    sent = await inline.send_2fa_request(req)
                except Exception as e:
                    log.error(f"Failed to send 2FA disable request: {e}")

            if not sent:
                log.error("Failed to send 2FA disable confirmation via inline bot")
                return web.json_response(
                    {"error": lang.get("web.errors.two_factor_send_failed", "Failed to send confirmation request to Telegram")},
                    status=503,
                )

            log.info("Web 2FA disable confirmation sent to Telegram")
            return web.json_response({
                "success": True,
                "pending": True,
                "enabled": True,
                "request_id": req.request_id,
            })

        try:
            save_2fa_setting(True)
        except Exception as e:
            return web.json_response({"success": True, "enabled": True, "warning": str(e)})

        log.info("Web 2FA updated: True")
        return web.json_response({"success": True, "enabled": True})

    async def get_2fa_status(self, request):
        """Returns the status of a pending 2FA change request."""
        req_id = request.query.get("req_id", "").strip()
        if not req_id:
            return web.json_response({"error": "missing req_id"}, status=400)

        req = two_factor_manager.get_request(req_id)
        if not req:
            return web.json_response({"status": "expired", "enabled": bool(config.web_auth_2fa)})

        return web.json_response({
            "status": req.status,
            "enabled": bool(config.web_auth_2fa),
        })

    async def set_language(self, request):
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": lang.get("web.errors.invalid_json", "Invalid JSON")}, status=400)

        lang_code = (data.get("language") or "").strip().lower()
        if lang_code not in SUPPORTED_LANGUAGES:
            return web.json_response({"error": lang.get("web.errors.unsupported_language", "Unsupported language")}, status=400)

        if not lang.set_language(lang_code):
            return web.json_response({"error": lang.get("web.errors.language_load_failed", "Failed to load language")}, status=500)

        config.language = lang_code
        os.environ["LANGUAGE"] = lang_code
        log.info(f"Language changed via web: {lang_code}")

        plugin_service = getattr(self.bot, "plugin_service", None) if self.bot else None
        if plugin_service and hasattr(plugin_service, "set_language"):
            plugin_service.set_language(lang_code)
        invalidate_web_caches(self.bot)

        try:
            self._save_language(lang_code)
        except Exception as e:
            return web.json_response({
                "success": True,
                "language": lang_code,
                "warning": lang.get("web.settings.language_env_warning", "Language changed, but .env was not saved: {error}").format(error=e),
            })

        return web.json_response({"success": True, "language": lang_code})

    async def export_tasks(self, request):
        if not self.bot:
            return web.json_response({"error": lang.get("web.errors.bot_not_connected", "Bot not connected")}, status=500)

        data = ExportImportService.export_tasks_data(self.bot.db)
        body = json.dumps(data, indent=2, ensure_ascii=False, default=str)
        return web.Response(
            text=body,
            content_type="application/json",
            headers={"Content-Disposition": 'attachment; filename="tasks_backup.json"'},
        )

    async def import_tasks(self, request):
        if not self.bot:
            return web.json_response({"error": lang.get("web.errors.bot_not_connected", "Bot not connected")}, status=500)

        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": lang.get("web.errors.invalid_json", "Invalid JSON")}, status=400)

        tasks = data.get("tasks") if isinstance(data, dict) else data
        if not isinstance(tasks, list):
            return web.json_response({"error": lang.get("web.errors.expected_task_array", "Expected a JSON array of tasks")}, status=400)

        count = ExportImportService.import_tasks_data(self.bot.db, tasks)
        invalidate_web_caches(self.bot)
        return web.json_response({"success": True, "count": count})

    async def clear_media_cache(self, request):
        if not self.bot:
            return web.json_response({"error": lang.get("web.errors.bot_not_connected", "Bot not connected")}, status=500)

        count = self.bot.clear_media_cache()
        return web.json_response({"success": True, "count": count})

    @staticmethod
    def _read_log_tail(log_file: str, limit: int):
        """Reads only the last ``limit`` lines instead of the whole file."""
        block_size = 64 * 1024
        with open(log_file, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            position = handle.tell()
            data = b""
            while position > 0 and data.count(b"\n") <= limit:
                step = min(block_size, position)
                position -= step
                handle.seek(position)
                data = handle.read(step) + data
        return data.decode("utf-8", errors="replace").splitlines()[-limit:]

    def _parse_logs(self, after: str, limit: int):
        logs = []
        log_file = config.log_file
        if not os.path.exists(log_file):
            return logs

        try:
            lines = self._read_log_tail(log_file, limit)
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                parts = line.split(" | ", 2)
                if len(parts) >= 3:
                    timestamp = parts[0]
                    level = parts[1].strip()
                    message = parts[2]
                    if after and timestamp <= after:
                        continue
                    time_part = timestamp.split(" ")[-1] if " " in timestamp else timestamp
                    logs.append({
                        "time": time_part,
                        "level": level,
                        "message": message,
                        "timestamp": timestamp,
                    })
        except Exception:
            pass

        return logs

    async def get_logs(self, request):
        after = request.query.get("after", "")
        try:
            limit = int(request.query.get("limit", "500"))
        except ValueError:
            limit = 500
        limit = min(max(limit, 1), 5000)

        logs = await asyncio.to_thread(self._parse_logs, after, limit)
        return web.json_response({"logs": logs})

    def register(self, app: web.Application):
        """Registers system and telemetry API routes."""
        app.router.add_get("/api/stats", self.get_stats)
        app.router.add_get("/api/bot", self.get_bot)
        app.router.add_get("/api/logs", self.get_logs)
        app.router.add_get("/api/settings", self.get_settings)
        app.router.add_post("/api/settings/2fa", self.set_2fa)
        app.router.add_get("/api/settings/2fa/status", self.get_2fa_status)
        app.router.add_post("/api/settings/language", self.set_language)
        app.router.add_get("/api/settings/export", self.export_tasks)
        app.router.add_post("/api/settings/import", self.import_tasks)
        app.router.add_post("/api/settings/clear-media", self.clear_media_cache)
        app.router.add_get("/ws/logs", websocket_logs)
