#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Scheduler service for parsing intervals, recurring schedules, and formatting time."""

import re
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple

from src.services.translator import lang


class SchedulerService:
    """Handles schedule parsing, next execution time calculation, and duration formatting."""

    @staticmethod
    def pluralize(number: int, one: str, few: str, many: str) -> str:
        """Inflects word based on integer value."""
        if number % 10 == 1 and number % 100 != 11:
            return one
        elif 2 <= number % 10 <= 4 and (number % 100 < 10 or number % 100 >= 20):
            return few
        else:
            return many

    @staticmethod
    def format_seconds(seconds: int) -> str:
        """Formats seconds into human-readable duration."""
        days_w = lang.get("time.days", "d")
        hours_w = lang.get("time.hours", "h")
        mins_w = lang.get("time.minutes", "m")
        secs_w = lang.get("time.seconds", "s")

        if seconds >= 86400:
            d = seconds // 86400
            h = (seconds % 86400) // 3600
            return f"{d}{days_w} {h}{hours_w}" if h else f"{d}{days_w}"
        if seconds >= 3600:
            h = seconds // 3600
            m = (seconds % 3600) // 60
            return f"{h}{hours_w} {m}{mins_w}" if m else f"{h}{hours_w}"
        if seconds >= 60:
            m = seconds // 60
            s = seconds % 60
            return f"{m}{mins_w} {s}{secs_w}" if s else f"{m}{mins_w}"
        return f"{seconds}{secs_w}"

    def parse_interval(self, interval_str: str) -> Optional[Dict[str, Any]]:
        """Parses human interval inputs like '5m', '1h30m', '10-30', '14:30@1,3,5'."""
        interval_str = interval_str.lower().strip()

        pattern = r"(\d+)([dhms])"
        matches = re.findall(pattern, interval_str)

        if matches and len(matches) > 1:
            total = 0
            for value, unit in matches:
                v = int(value)
                if unit == "d":
                    total += v * 86400
                elif unit == "h":
                    total += v * 3600
                elif unit == "m":
                    total += v * 60
                elif unit == "s":
                    total += v
            if total >= 5:
                return {"type": "interval", "seconds": total}

        if "@" in interval_str:
            time_part, days_part = interval_str.split("@")
            times = []
            for t in time_part.split(","):
                t = t.strip()
                if ":" in t:
                    parts = t.split(":")
                    if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                        hour, minute = int(parts[0]), int(parts[1])
                        if 0 <= hour <= 23 and 0 <= minute <= 59:
                            times.append({"hour": hour, "minute": minute})
            if not times:
                return None

            days = []
            if "-" in days_part:
                start, end = map(int, days_part.split("-"))
                days = list(range(start, end + 1))
            elif "," in days_part:
                days = [int(d.strip()) for d in days_part.split(",") if d.strip().isdigit()]
            else:
                days = [int(days_part)] if days_part.isdigit() else []

            if not days:
                return None
            return {"type": "schedule_multi", "times": times, "days": days}

        if ":" in interval_str:
            times = []
            for t in interval_str.split(","):
                t = t.strip()
                if ":" in t:
                    parts = t.split(":")
                    if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                        hour, minute = int(parts[0]), int(parts[1])
                        if 0 <= hour <= 23 and 0 <= minute <= 59:
                            times.append({"hour": hour, "minute": minute})
            if times:
                return {"type": "schedule_multi", "times": times, "days": [1, 2, 3, 4, 5, 6, 7]}

        if "-" in interval_str and not any(c.isalpha() for c in interval_str):
            parts = interval_str.split("-")
            if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                min_int, max_int = int(parts[0]), int(parts[1])
                if min_int >= 5 and max_int > min_int:
                    return {"type": "random", "min": min_int, "max": max_int}

        if interval_str.endswith("s"):
            return {"type": "interval", "seconds": max(5, int(interval_str[:-1]))}
        elif interval_str.endswith("m"):
            return {"type": "interval", "seconds": max(5, int(interval_str[:-1]) * 60)}
        elif interval_str.endswith("h"):
            return {"type": "interval", "seconds": max(5, int(interval_str[:-1]) * 3600)}
        elif interval_str.endswith("d"):
            return {"type": "interval", "seconds": max(5, int(interval_str[:-1]) * 86400)}
        elif interval_str.endswith("w"):
            return {"type": "interval", "seconds": max(5, int(interval_str[:-1]) * 604800)}
        elif interval_str.isdigit():
            return {"type": "interval", "seconds": max(5, int(interval_str))}

        return None

    def parse_once(self, once_str: str) -> Optional[datetime]:
        """Parses once execution time format like 'YYYY-MM-DD HH:MM' or '+30m'."""
        once_str = once_str.strip().lower()

        if re.match(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", once_str):
            try:
                target = datetime.strptime(once_str, "%Y-%m-%d %H:%M")
                if target > datetime.now():
                    return target
            except Exception:
                pass

        if once_str.startswith("+"):
            try:
                cleaned = once_str.replace(" ", "")
                match = re.match(r"\+(\d+)([smhd]?)", cleaned)
                if match:
                    val = int(match.group(1))
                    unit = match.group(2) or "s"
                    if unit == "s":
                        delta = timedelta(seconds=val)
                    elif unit == "m":
                        delta = timedelta(minutes=val)
                    elif unit == "h":
                        delta = timedelta(hours=val)
                    elif unit == "d":
                        delta = timedelta(days=val)
                    else:
                        return None
                    return (datetime.now() + delta).replace(microsecond=0)
            except Exception:
                pass

        return None

    def format_interval(self, cfg: Optional[Dict[str, Any]], is_once: int = 0) -> str:
        """Formats interval configuration for display."""
        if is_once:
            return lang.get("task.once", "Once")

        if cfg is None:
            return "—"

        cfg_type = cfg.get("type")
        if cfg_type == "schedule_multi":
            times = cfg.get("times", [])
            days = cfg.get("days", [])
            time_str = ", ".join([f"{t['hour']:02d}:{t['minute']:02d}" for t in times])

            days_names = {
                1: lang.get("days.mon", "Mon"),
                2: lang.get("days.tue", "Tue"),
                3: lang.get("days.wed", "Wed"),
                4: lang.get("days.thu", "Thu"),
                5: lang.get("days.fri", "Fri"),
                6: lang.get("days.sat", "Sat"),
                7: lang.get("days.sun", "Sun"),
            }

            if len(days) == 7:
                day_str = lang.get("days.every_day", "daily")
            elif days == [1, 2, 3, 4, 5]:
                day_str = lang.get("days.weekdays", "weekdays")
            elif days == [6, 7]:
                day_str = lang.get("days.weekend", "weekend")
            else:
                day_str = ",".join([days_names.get(d, str(d)) for d in sorted(days)])

            return f"{time_str} ({day_str})"

        elif cfg_type == "random":
            return f"{self.format_seconds(cfg['min'])}-{self.format_seconds(cfg['max'])}"

        else:
            secs = cfg.get("seconds", 0)
            if secs == 0:
                return lang.get("task.once", "Once")
            return f"{self.format_seconds(secs)}"

    def get_next_schedule_time(self, interval_config: Dict[str, Any]) -> Tuple[datetime, Dict[str, int]]:
        """Calculates next upcoming datetime for schedule_multi."""
        now = datetime.now()
        current_weekday = now.isoweekday()

        times = interval_config.get("times", [])
        days = interval_config.get("days", [])

        sorted_times = sorted(times, key=lambda x: (x["hour"], x["minute"]))

        for t in sorted_times:
            target = now.replace(hour=t["hour"], minute=t["minute"], second=0, microsecond=0)
            diff = (target - now).total_seconds()
            if diff > 1 and current_weekday in days:
                return target, t

        for day_offset in range(1, 8):
            next_weekday = current_weekday + day_offset
            if next_weekday > 7:
                next_weekday -= 7

            if next_weekday in days:
                first_time = min(sorted_times, key=lambda x: (x["hour"], x["minute"]))
                target = now.replace(hour=first_time["hour"], minute=first_time["minute"], second=0, microsecond=0)
                target += timedelta(days=day_offset)
                return target, first_time

        return now + timedelta(days=1), sorted_times[0]
