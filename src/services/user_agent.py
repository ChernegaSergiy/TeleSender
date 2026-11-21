#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Lightweight User-Agent parser.

Turns a raw ``User-Agent`` header into a short, human readable summary such as
``Chrome 131 · Android 10+ · Samsung SM-G991B`` instead of the noisy raw string.

Implemented with plain regular expressions so no extra dependency is required.
The parser is intentionally forgiving: unknown parts are simply omitted, so a
partial match still produces something useful.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional, Tuple

# ---------------------------------------------------------------------------
# Detection tables
# ---------------------------------------------------------------------------

# Browsers, most specific token first (Edge/Opera/Samsung all embed "Chrome").
_BROWSERS = (
    (r"EdgiOS/([\d.]+)", "Edge"),
    (r"EdgA?/([\d.]+)", "Edge"),
    (r"OPR/([\d.]+)", "Opera"),
    (r"Opera Mini/([\d.]+)", "Opera Mini"),
    (r"Opera Mobi/([\d.]+)", "Opera Mobile"),
    (r"YaBrowser/([\d.]+)", "Yandex"),
    (r"SamsungBrowser/([\d.]+)", "Samsung Internet"),
    (r"Vivaldi/([\d.]+)", "Vivaldi"),
    (r"DuckDuckGo/([\d.]+)", "DuckDuckGo"),
    (r"Firefox/([\d.]+)", "Firefox"),
    (r"FxiOS/([\d.]+)", "Firefox"),
    (r"CriOS/([\d.]+)", "Chrome"),
    (r"HeadlessChrome/([\d.]+)", "Chrome Headless"),
    (r"Chrome/([\d.]+)", "Chrome"),
    (r"Chromium/([\d.]+)", "Chromium"),
    (r"Version/([\d.]+)[\d.]*\s+.*Safari/", "Safari"),
    (r"Safari/([\d.]+)", "Safari"),
)

# Non-browser clients (checked before browsers).
_CLIENTS = (
    (r"TelegramBot", "Telegram Bot"),
    (r"Telegram", "Telegram"),
    (r"Instagram", "Instagram"),
    (r"FBAN|FBAV", "Facebook"),
    (r"okhttp/([\d.]+)", "okhttp"),
    (r"python-requests/([\d.]+)", "python-requests"),
    (r"python-urllib", "python-urllib"),
    (r"aiohttp/([\d.]+)", "aiohttp"),
    (r"curl/([\d.]+)", "curl"),
    (r"Wget/([\d.]+)", "Wget"),
    (r"Go-http-client", "Go HTTP client"),
    (r"PostmanRuntime/([\d.]+)", "Postman"),
    (r"axios/([\d.]+)", "axios"),
    (r"node-fetch", "node-fetch"),
)

# Device brand guessed from the Android model code.
_BRANDS = (
    (r"^(SM-|SAMSUNG|GT-|SCH-|SGH-)", "Samsung"),
    (r"^(Pixel|Nexus)", "Google"),
    (r"^(Redmi|POCO|MIX|MI\b|Mi\b|HM\s|M2\d{3}|M1\d{3})", "Xiaomi"),
    (r"^(ONEPLUS|IN\d{4}|LE\d{4}|NE\d{4}|KB\d{4}|HD\d{4}|GM\d{4})", "OnePlus"),
    (r"^(CPH\d|OPPO|P[A-Z]{2}\d)", "OPPO"),
    (r"^(vivo|VIVO|V\d{4})", "vivo"),
    (r"^RMX", "realme"),
    (r"^(HUAWEI|ANE-|ELS-|VOG-|JNY-|FIG-|ELE-|LIO-|CLT-|MAR-)", "Huawei"),
    (r"^HONOR", "Honor"),
    (r"^(Moto|XT\d)", "Motorola"),
    (r"^LG-", "LG"),
    (r"^Nokia", "Nokia"),
    (r"^(Sony|XQ-)", "Sony"),
    (r"^(ASUS|Z00|ZS\d|ZB\d)", "ASUS"),
    (r"^Lenovo", "Lenovo"),
    (r"^(TECNO|Infinix|itel)", "Transsion"),
    (r"^Nothing", "Nothing"),
    (r"^HTC", "HTC"),
)

_WINDOWS_VERSIONS = {
    "10.0": "10/11",
    "6.3": "8.1",
    "6.2": "8",
    "6.1": "7",
    "6.0": "Vista",
    "5.2": "XP x64",
    "5.1": "XP",
}

_BOT_RE = re.compile(r"(?:^|[\s;(])[\w.-]*bot\b|crawler|spider|slurp", re.I)
_BOT_TOKEN_RE = re.compile(r"(?:^|[\s;(])([\w.-]*bot)\b", re.I)

# Segments inside the Android UA parenthesis that are never a device model.
_ANDROID_SKIP = {"U", "wv", "K", "Mobile"}

_LOCALE_RE = re.compile(r"[a-zA-Z]{2}[-_][a-zA-Z]{2}")


# ---------------------------------------------------------------------------
# Parsed representation
# ---------------------------------------------------------------------------


@dataclass
class UserAgentInfo:
    """Structured result of :func:`parse`."""

    raw: str = ""
    browser: Optional[str] = None
    browser_version: Optional[str] = None
    os: Optional[str] = None
    os_version: Optional[str] = None
    device: Optional[str] = None
    device_type: str = "unknown"  # mobile | tablet | desktop | bot | unknown
    is_bot: bool = False
    reduced: bool = False  # UA reduction hid the device model / real OS version
    extras: dict = field(default_factory=dict)

    def summary(self, separator: str = " · ", hidden_label: Optional[str] = None) -> str:
        """Build a compact human readable string, e.g. ``Chrome 131 · Android 10+``."""
        parts = []
        if self.browser:
            parts.append(f"{self.browser} {self.browser_version or ''}".strip())
        if self.os:
            parts.append(f"{self.os} {self.os_version or ''}".strip())
        if self.device:
            parts.append(self.device)
        elif self.reduced and hidden_label:
            parts.append(hidden_label)
        return separator.join(parts)

    def details(self) -> str:
        """Multi-line detail block (browser / OS / device), empty parts omitted."""
        lines = []
        if self.browser:
            lines.append(f"Browser: {self.browser} {self.browser_version or ''}".strip())
        if self.os:
            lines.append(f"OS: {self.os} {self.os_version or ''}".strip())
        if self.device:
            lines.append(f"Device: {self.device} ({self.device_type})")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _major(version: Optional[str]) -> Optional[str]:
    if not version:
        return None
    return version.split(".")[0]


def _brand(model: str) -> Optional[str]:
    for pattern, name in _BRANDS:
        if re.match(pattern, model):
            return name
    return None


def _android_model(ua: str) -> Optional[str]:
    match = re.search(r"Android[^;)]*;(.*?)\)", ua)
    if not match:
        return None
    for segment in (part.strip() for part in match.group(1).split(";")):
        if not segment or segment in _ANDROID_SKIP:
            continue
        if _LOCALE_RE.fullmatch(segment):
            continue  # e.g. "en-us"
        model = segment.split(" Build/")[0].strip()
        if not model or model == "K":
            continue
        return model
    return None


def _bot_name(ua: str) -> Optional[str]:
    match = _BOT_TOKEN_RE.search(ua)
    if not match:
        return None
    token = match.group(1)
    return token[:1].upper() + token[1:]


def _parse_browser(ua: str) -> Tuple[Optional[str], Optional[str]]:
    # Android WebView shares the Chrome token but marks itself with "; wv)".
    if re.search(r";\s*wv\)", ua):
        match = re.search(r"Chrome/([\d.]+)", ua)
        return "Android WebView", _major(match.group(1)) if match else None

    for pattern, name in _CLIENTS:
        match = re.search(pattern, ua)
        if match:
            version = match.group(1) if match.groups() else None
            return name, version

    for pattern, name in _BROWSERS:
        match = re.search(pattern, ua)
        if match:
            return name, _major(match.group(1))

    return None, None


def _parse_os(ua: str) -> Tuple[Optional[str], Optional[str]]:
    match = re.search(r"Windows NT ([\d.]+)", ua)
    if match:
        return "Windows", _WINDOWS_VERSIONS.get(match.group(1), match.group(1))

    # iOS check must run before macOS: iOS UAs contain "like Mac OS X".
    match = re.search(r"(?:iPhone|iPad|iPod).*?OS ([\d_]+)", ua)
    if match:
        return "iOS", match.group(1).replace("_", ".")

    match = re.search(r"Android ([\d.]+)", ua)
    if match:
        version = match.group(1)
        # Chrome UA reduction freezes the version at 10 and hides the model
        # behind "K"; the real version is unknown, so 10 is only a floor.
        if version in ("10", "10.0") and re.search(r"Android 10(?:\.0)?;\s*K\)", ua):
            version = "10+"
        return "Android", version

    match = re.search(r"CrOS \S+ ([\d.]+)", ua)
    if match:
        return "ChromeOS", match.group(1)

    match = re.search(r"Mac OS X ([\d_.]+)", ua)
    if match:
        return "macOS", match.group(1).replace("_", ".")

    if "Ubuntu" in ua:
        return "Ubuntu", None
    if "Linux" in ua:
        return "Linux", None
    if "FreeBSD" in ua:
        return "FreeBSD", None
    return None, None


def _parse_device(ua: str) -> Tuple[Optional[str], str]:
    if "iPad" in ua:
        return "iPad", "tablet"
    if "iPhone" in ua:
        return "iPhone", "mobile"
    if "iPod" in ua:
        return "iPod", "mobile"
    if "Android" in ua:
        model = _android_model(ua)
        if model:
            brand = _brand(model)
            if brand and brand.lower() in model.lower():
                device = model
            elif brand:
                device = f"{brand} {model}"
            else:
                device = model
        else:
            device = None
        kind = "tablet" if ("Tablet" in ua or (model and "Tab" in model)) else "mobile"
        return device, kind
    if "CrOS" in ua:
        return "Chromebook", "desktop"
    if "Macintosh" in ua:
        return "Mac", "desktop"
    if "Windows" in ua:
        return None, "desktop"
    if "Linux" in ua:
        return None, "desktop"
    return None, "unknown"


# ---------------------------------------------------------------------------
# Client Hints support (Sec-CH-UA-*)
# ---------------------------------------------------------------------------

# Chromium only sends these high-entropy hints over HTTPS and only after the
# server asks for them with an ``Accept-CH`` response header.
def _clean_hint(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    cleaned = value.strip().strip('"').strip()
    return cleaned or None


def _apply_hints(info: UserAgentInfo, hints: dict) -> None:
    """Merge ``Sec-CH-UA-*`` values into a parsed UA, preferring real data."""
    platform = _clean_hint(hints.get("platform"))
    platform_version = _clean_hint(hints.get("platform_version"))
    model = _clean_hint(hints.get("model"))
    mobile = _clean_hint(hints.get("mobile"))

    if platform and not info.os:
        info.os = platform

    if platform_version and info.os == "Android":
        # "14.0.0" -> "14"; this is the real version the UA hides as "10+".
        info.os_version = platform_version.split(".")[0]

    if model and info.os == "Android":
        brand = _brand(model)
        if brand and brand.lower() not in model.lower():
            info.device = f"{brand} {model}"
        else:
            info.device = model
        info.device_type = "tablet" if mobile == "?0" else "mobile"

    if info.device:
        info.reduced = False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse(user_agent: Optional[str], hints: Optional[dict] = None) -> UserAgentInfo:
    """Parse a raw ``User-Agent`` string into :class:`UserAgentInfo`.

    ``hints`` may contain ``Sec-CH-UA-*`` values (``platform``,
    ``platform_version``, ``model``, ``mobile``) which are preferred over the
    reduced UA string when present.
    """
    ua = (user_agent or "").strip()
    info = UserAgentInfo(raw=ua)
    if not ua:
        if hints:
            _apply_hints(info, hints)
        return info

    info.browser, info.browser_version = _parse_browser(ua)
    info.os, info.os_version = _parse_os(ua)
    info.device, info.device_type = _parse_device(ua)
    info.is_bot = bool(_BOT_RE.search(ua)) or info.browser in ("Telegram Bot",)
    info.reduced = (
        info.os == "Android" and info.os_version == "10+" and not info.device
    )

    if info.is_bot:
        info.device_type = "bot"
        info.device = None
        info.reduced = False
        if not info.browser:
            info.browser = _bot_name(ua) or "Bot"

    if hints:
        _apply_hints(info, hints)

    return info


def describe(
    user_agent: Optional[str],
    unknown: str = "unknown",
    hints: Optional[dict] = None,
    hidden_label: Optional[str] = None,
) -> str:
    """Return a short human readable summary, falling back to ``unknown``."""
    summary = parse(user_agent, hints=hints).summary(hidden_label=hidden_label)
    return summary or unknown


__all__ = ["UserAgentInfo", "parse", "describe"]
