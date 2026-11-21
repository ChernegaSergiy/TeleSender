#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
localization.py - Plugin localization system
"""

import os
import json
from typing import Dict, Optional, Any, List
import logging


class PluginLocalization:
    """
    Localization system for an individual plugin.

    Loads JSON files from the plugin's lang/ folder.
    """

    def __init__(self, plugin_name: str, lang_dir: str, default_lang: str = 'en'):
        """
        Plugin localization initialization.

        Args:
            plugin_name: Plugin name
            lang_dir: Path to the lang/ folder
            default_lang: Default language
        """
        self.plugin_name = plugin_name
        self.lang_dir = lang_dir
        self.default_lang = default_lang
        self.current_lang = default_lang
        self._strings: Dict[str, Dict[str, str]] = {}
        self._loaded_langs: set = set()
        self.logger = logging.getLogger(f'localization.{plugin_name}')

        self._load_all()

    def _load_all(self) -> None:
        """Loads all available language files"""
        if not os.path.exists(self.lang_dir):
            self.logger.warning(f"Lang directory not found: {self.lang_dir}")
            return

        for filename in os.listdir(self.lang_dir):
            if filename.endswith('.json'):
                lang_code = filename[:-5]
                self._load_lang(lang_code)

    def _load_lang(self, lang_code: str) -> bool:
        """
        Loads a specific language file.

        Args:
            lang_code: Language code, for example uk, en, pl or ja

        Returns:
            True on success
        """
        lang_file = os.path.join(self.lang_dir, f'{lang_code}.json')
        if not os.path.exists(lang_file):
            return False

        try:
            with open(lang_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            self._strings[lang_code] = data
            self._loaded_langs.add(lang_code)
            return True
        except Exception as e:
            self.logger.error(f"Failed to load {lang_file}: {e}")
            return False

    def set_language(self, lang_code: str) -> bool:
        """
        Sets the active language.

        Args:
            lang_code: Language code

        Returns:
            True if the language is available
        """
        if lang_code not in self._loaded_langs:
            # Try to load
            if not self._load_lang(lang_code):
                return False

        self.current_lang = lang_code
        return True

    def get(self, key: str, **kwargs) -> str:
        """
        Gets a translation by key.

        Args:
            key: Translation key (for example, "ping" or "errors.timeout")
            **kwargs: Formatting parameters

        Returns:
            Translated text
        """
        # Nested key (dot-separated)
        if '.' in key:
            return self._get_nested(key, **kwargs)

        # Simple key
        text = self._get_simple(key)

        if kwargs:
            try:
                return text.format(**kwargs)
            except (KeyError, ValueError):
                pass

        return text

    def _get_simple(self, key: str) -> str:
        """Gets a simple key"""
        # Current language
        if self.current_lang in self._strings:
            if key in self._strings[self.current_lang]:
                return self._strings[self.current_lang][key]

        # Fallback to English
        if 'en' in self._strings:
            if key in self._strings['en']:
                return self._strings['en'][key]

        # If not found anywhere, return the key
        return key

    def _get_nested(self, key: str, **kwargs) -> str:
        """Gets a nested key (dot-separated)"""
        parts = key.split('.')

        # Current language first
        if self.current_lang in self._strings:
            value = self._get_nested_from_dict(self._strings[self.current_lang], parts)
            if value is not None:
                if kwargs and isinstance(value, str):
                    try:
                        return value.format(**kwargs)
                    except (KeyError, ValueError):
                        pass
                return value

        # Fallback to English
        if 'en' in self._strings:
            value = self._get_nested_from_dict(self._strings['en'], parts)
            if value is not None:
                if kwargs and isinstance(value, str):
                    try:
                        return value.format(**kwargs)
                    except (KeyError, ValueError):
                        pass
                return value

        return key

    def _get_nested_from_dict(self, data: Dict, parts: List[str]) -> Optional[str]:
        """Retrieves a nested value from a dictionary"""
        current = data
        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return None
        return current if isinstance(current, str) else None

    def get_raw(self, lang_code: str) -> Dict[str, str]:
        """
        Gets all translations for a specific language.

        Args:
            lang_code: Language code

        Returns:
            Translation dictionary
        """
        return self._strings.get(lang_code, {})

    def get_all_langs(self) -> list:
        """
        Gets a list of available languages.

        Returns:
            List of language codes
        """
        return list(self._loaded_langs)

    def reload(self) -> None:
        """Reloads all language files"""
        self._strings.clear()
        self._loaded_langs.clear()
        self._load_all()


class CoreLocalization:
    """
    Localization for the UserBot core.
    Uses global language files from the languages/ folder
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialize()
        return cls._instance

    def _initialize(self):
        self.langs_dir = 'languages'
        self.current_lang = 'uk'
        self._strings: Dict[str, Dict[str, str]] = {}
        self._loaded_langs = set()
        self.logger = logging.getLogger('CoreLocalization')
        self._load_all()

    def _load_all(self) -> None:
        """Loads all core language files"""
        if not os.path.exists(self.langs_dir):
            return

        for filename in os.listdir(self.langs_dir):
            if filename.endswith('.json'):
                lang_code = filename[:-5]
                self._load_lang(lang_code)

    def _load_lang(self, lang_code: str) -> bool:
        """Loads a core language file"""
        lang_file = os.path.join(self.langs_dir, f'{lang_code}.json')
        if not os.path.exists(lang_file):
            return False

        try:
            with open(lang_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            self._strings[lang_code] = data
            self._loaded_langs.add(lang_code)
            return True
        except Exception as e:
            self.logger.error(f"Failed to load {lang_file}: {e}")
            return False

    def set_language(self, lang_code: str) -> bool:
        """Sets the core active language"""
        if lang_code not in self._loaded_langs:
            if not self._load_lang(lang_code):
                return False
        self.current_lang = lang_code
        return True

    def get(self, key: str, **kwargs) -> str:
        """Gets a translation by key"""
        if '.' in key:
            return self._get_nested(key, **kwargs)
        return self._get_simple(key, **kwargs)

    def _get_simple(self, key: str, **kwargs) -> str:
        """Gets a simple key"""
        # Current language
        if self.current_lang in self._strings:
            if key in self._strings[self.current_lang]:
                text = self._strings[self.current_lang][key]
                if kwargs:
                    try:
                        return text.format(**kwargs)
                    except (KeyError, ValueError):
                        pass
                return text

        # Fallback to English
        if 'en' in self._strings:
            if key in self._strings['en']:
                text = self._strings['en'][key]
                if kwargs:
                    try:
                        return text.format(**kwargs)
                    except (KeyError, ValueError):
                        pass
                return text

        return key

    def _get_nested(self, key: str, **kwargs) -> str:
        """Gets a nested key"""
        parts = key.split('.')

        # Current language
        if self.current_lang in self._strings:
            value = self._get_nested_from_dict(self._strings[self.current_lang], parts)
            if value is not None:
                if kwargs and isinstance(value, str):
                    try:
                        return value.format(**kwargs)
                    except (KeyError, ValueError):
                        pass
                return value

        # Fallback to English
        if 'en' in self._strings:
            value = self._get_nested_from_dict(self._strings['en'], parts)
            if value is not None:
                if kwargs and isinstance(value, str):
                    try:
                        return value.format(**kwargs)
                    except (KeyError, ValueError):
                        pass
                return value

        return key

    def _get_nested_from_dict(self, data: Dict, parts: List[str]) -> Optional[str]:
        """Retrieves a nested value"""
        current = data
        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return None
        return current if isinstance(current, str) else None

    def get_all_langs(self) -> list:
        """Gets a list of available languages"""
        return list(self._loaded_langs)

    def reload(self) -> None:
        """Reloads all core language files"""
        self._strings.clear()
        self._loaded_langs.clear()
        self._load_all()
