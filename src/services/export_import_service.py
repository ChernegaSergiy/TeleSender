#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Service for exporting and importing task configurations to/from JSON."""

import json
import os
from typing import Any, List


class ExportImportService:
    """Manages serializing task definitions to JSON and bulk importing."""

    @staticmethod
    def export_tasks_data(db) -> List[dict]:
        """Builds a serializable list of all task entries."""
        tasks = db.get_all()
        export_data = []
        for t in tasks:
            export_data.append({
                "id": t[0],
                "chat_id": t[1],
                "message": t[2],
                "media_path": t[3],
                "media_type": t[4],
                "interval": t[5],
                "is_active": t[6],
                "total_sent": t[7],
                "last_sent": t[8],
                "is_once": t[9] if len(t) > 9 else 0,
                "once_time": t[10] if len(t) > 10 else None,
                "messages_list": t[12] if len(t) > 12 else None,
                "chats_list": t[13] if len(t) > 13 else None,
            })
        return export_data

    @staticmethod
    def export_tasks(db, filename: str = "tasks_backup.json") -> int:
        """Exports all task entries to JSON."""
        export_data = ExportImportService.export_tasks_data(db)
        if not export_data:
            return 0

        try:
            with open(filename, "w", encoding="utf-8") as f:
                json.dump(export_data, f, indent=2, ensure_ascii=False, default=str)
            return len(export_data)
        except Exception:
            return 0

    @staticmethod
    def import_tasks_data(db, import_data: List[dict]) -> int:
        """Imports tasks from an already parsed JSON list."""
        if not isinstance(import_data, list):
            return 0

        count = 0
        for task in import_data:
            if not isinstance(task, dict):
                continue
            db.add(
                chat_id=task.get("chat_id"),
                message=task.get("message", ""),
                media_path=task.get("media_path"),
                media_type=task.get("media_type"),
                interval_config=task.get("interval", {"type": "interval", "seconds": 60}),
                is_once=task.get("is_once", 0),
                once_time=task.get("once_time"),
                messages_list=task.get("messages_list"),
                chats_list=task.get("chats_list"),
            )
            count += 1
        return count

    @staticmethod
    def import_tasks(db, filename: str = "tasks_backup.json") -> int:
        """Imports tasks from a JSON backup file."""
        if not os.path.exists(filename):
            return 0

        try:
            with open(filename, "r", encoding="utf-8") as f:
                import_data = json.load(f)
            return ExportImportService.import_tasks_data(db, import_data)
        except Exception:
            return 0
