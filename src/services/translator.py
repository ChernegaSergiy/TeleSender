#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Localization and translation service."""

import json
import os
import re
from typing import Any, Dict, Optional
from src.config.config import config


# Central language registry. Native names are shown in both the console and
# the web interface, so there is a single source of truth for supported codes.
SUPPORTED_LANGUAGES = {
    "uk": "Українська",
    "en": "English",
    "ru": "Русский",
    "pl": "Polski",
    "de": "Deutsch",
    "es": "Español",
    "fr": "Français",
    "pt": "Português",
    "tr": "Türkçe",
    "id": "Indonesia",
    "it": "Italiano",
    "ja": "日本語",
    "ko": "한국어",
}

LANGUAGE_CODES = tuple(SUPPORTED_LANGUAGES)


def language_from_choice(choice: str) -> Optional[str]:
    """Maps a 1-based menu choice to a supported language code."""
    if choice.isdigit():
        index = int(choice) - 1
        if 0 <= index < len(LANGUAGE_CODES):
            return LANGUAGE_CODES[index]
    return None


def format_language_choices() -> str:
    """Returns the numbered language list for console menus."""
    return "\n".join(
        f"{index} - {name}"
        for index, (_, name) in enumerate(SUPPORTED_LANGUAGES.items(), 1)
    )


class Translator:
    """Singleton localization manager."""

    _instance = None
    _current_lang = "en"
    _strings: Dict[str, Any] = {}
    _fallback: Dict[str, Any] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._load_language()
        return cls._instance

    def _load_language(self, lang_code: Optional[str] = None):
        """Loads translations from json file."""
        if lang_code:
            self._current_lang = lang_code
        else:
            self._current_lang = config.language or "en"

        lang_file = os.path.join("languages", f"{self._current_lang}.json")

        if not os.path.exists(lang_file):
            print(f"Language '{self._current_lang}' not found, falling back to 'en'")
            lang_file = os.path.join("languages", "en.json")
            self._current_lang = "en"

        try:
            if os.path.exists(lang_file):
                with open(lang_file, "r", encoding="utf-8") as f:
                    self._strings = json.load(f)
            else:
                self._strings = {}
            # English is the safety net for keys missing from a translation.
            if self._current_lang != "en":
                en_file = os.path.join("languages", "en.json")
                if os.path.exists(en_file):
                    with open(en_file, "r", encoding="utf-8") as f:
                        self._fallback = json.load(f)
                else:
                    self._fallback = {}
            else:
                self._fallback = {}
            return True
        except Exception as e:
            print(f"Failed to load language file: {e}")
            self._strings = {}
            self._fallback = {}
            return False

    def set_language(self, lang_code: str) -> bool:
        """Sets active language and reloads translations."""
        if lang_code in SUPPORTED_LANGUAGES:
            self._current_lang = lang_code
            self._load_language(lang_code)
            return True
        return False

    def reload(self) -> bool:
        """Reloads current translations from file."""
        return self._load_language()

    def get(self, key: str, default: Optional[str] = None) -> Any:
        """Gets translated string by dot-separated key with an English fallback."""
        keys = key.split(".")

        def lookup(source: Dict[str, Any]):
            val = source
            for k in keys:
                if isinstance(val, dict) and k in val:
                    val = val[k]
                else:
                    return None
            return val

        val = lookup(self._strings)
        if val is not None:
            return val
        val = lookup(self._fallback)
        if val is not None:
            return val
        return default if default is not None else key

    def get_lang(self) -> str:
        return self._current_lang

    def get_strings(self) -> Dict[str, Any]:
        return self._strings


lang = Translator()
