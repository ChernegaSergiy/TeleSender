#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
plugin.py - Base class for all plugins
"""

import asyncio
import logging
from typing import Any, Dict, Optional, Callable, List
from abc import ABC, abstractmethod


def generate_plugin_id(name: str, author: str) -> str:
    """Generates a unique plugin ID"""
    name_clean = name.lower().replace(' ', '_')
    author_clean = author.lower().replace(' ', '_')
    return f"{name_clean}.{author_clean}"


class Plugin(ABC):
    """
    Base class for all plugins.
    Metadata is taken from the manifest.
    """

    def __init__(self, client=None, db=None, bot=None, manifest: Dict = None, config: Dict = None):
        self.client = client
        self.db = db
        self.bot = bot
        self.inline = None  # InlineManager — injected by the loader after init
        self.logger = logging.getLogger(f'plugin.{self.__class__.__name__}')
        self._loaded = False
        self._handlers: List[Dict] = []
        self._loop_tasks: List[asyncio.Task] = []
        self._localization = None
        self.path: Optional[str] = None
        self.folder_name: Optional[str] = None

        # Metadata from the manifest
        self._manifest = manifest or {}
        self._load_manifest()

        # Configuration from config.json
        self._default_config = config or {}
        self._config_data = self._default_config.copy()
        self._load_config()

        # Configuration from config.json
        self._default_config = config.copy() if config else {}

        # Remove _version from default_config (it is a service field)
        self._default_config.pop('_version', None)

        self._config_data = self._default_config.copy()
        self._load_config()

        # Add _version to config_data
        self._config_data['_version'] = self.version

    def _load_manifest(self):
        """Loads metadata from the manifest"""
        self.plugin_id = self._manifest.get('id', 'unknown')
        self.name = self._manifest.get('name', self.__class__.__name__)
        self.author = self._manifest.get('author', 'Unknown')
        self.version = self._manifest.get('version', '0.1')
        self.description = self._manifest.get('description', 'No description')
        self.category = self._manifest.get('category', 'General')
        self.requirements = self._manifest.get('requirements', [])

    def get_manifest(self) -> Dict:
        """Returns the full manifest"""
        return {
            'id': self.plugin_id,
            'name': self.name,
            'author': self.author,
            'version': self.version,
            'description': self.description,
            'category': self.category,
            'requirements': self.requirements
        }

    def _load_config(self):
        """Loads the plugin configuration from the database"""
        if not self.db:
            return

        try:
            if hasattr(self.db, "get_plugin_config"):
                raw = self.db.get_plugin_config(self.plugin_id)
            else:
                cursor = self.db.cursor
                cursor.execute(
                    "SELECT config FROM plugin_config WHERE id = ?",
                    (self.plugin_id,)
                )
                result = cursor.fetchone()
                raw = result[0] if result else None

            if raw:
                import json
                saved = json.loads(raw)
                if isinstance(saved, dict):
                    for key, value in saved.items():
                        if key == '_version':
                            continue  # skip the version
                        if key in self._config_data:
                            self._config_data[key] = value
        except Exception as e:
            self.logger.warning(f"Failed to load config: {e}")

    def save_config(self):
        """Saves the plugin configuration to the database"""
        if not self.db:
            return

        try:
            import json
            # Copy and update the version
            data_to_save = self._config_data.copy()
            data_to_save['_version'] = self.version  #  current version

            if hasattr(self.db, "save_plugin_config"):
                self.db.save_plugin_config(self.plugin_id, json.dumps(data_to_save))
            else:
                cursor = self.db.cursor
                cursor.execute(
                    """INSERT OR REPLACE INTO plugin_config (id, config) 
                       VALUES (?, ?)""",
                    (self.plugin_id, json.dumps(data_to_save))
                )
                self.db.conn.commit()
        except Exception as e:
            self.logger.warning(f"Failed to save config: {e}")

    def get_config(self, key: str, default: Any = None) -> Any:
        """Gets a configuration value"""
        return self._config_data.get(key, default)

    def set_config(self, key: str, value: Any) -> bool:
        """Sets a configuration value"""
        if key not in self._config_data:
            return False
        self._config_data[key] = value
        self.save_config()
        return True

    def set_localization(self, localization):
        """Sets the localization system for the plugin"""
        self._localization = localization

    def t(self, key: str, **kwargs) -> str:
        """Gets a translation for the plugin"""
        if self._localization:
            return self._localization.get(key, **kwargs)
        return key

    async def on_load(self) -> None:
        """Called when the plugin is loaded"""
        pass

    async def on_unload(self) -> None:
        """Called when the plugin is unloaded"""
        pass

    async def on_page_render(self, page_path: str, request) -> Dict[str, Any]:
        """Returns extra values for a plugin web page render context."""
        return {}

    def get_menu_items(self) -> List[Dict[str, Any]]:
        """Returns navigation entries for the main web interface.

        Each item is a dict with ``name``, ``url`` and an optional ``icon``.
        The icon may be a Lucide icon name (``"sparkles"``), an absolute
        URL/path, or an image file inside the plugin's ``static/`` folder
        (``"icon.svg"``).
        """
        return []

    async def on_config_change(self, key: str, old_value: Any, new_value: Any) -> None:
        """Called when the configuration changes"""
        pass

    def register_handler(self, handler_type: str, handler: Callable, **kwargs):
        """Registers a handler for automatic addition"""
        self._handlers.append({
            'type': handler_type,
            'handler': handler,
            'kwargs': kwargs
        })

    def register_loop(self, coro: Callable, interval: float = 60.0):
        """Registers a loop task"""
        self._handlers.append({
            'type': 'loop',
            'handler': coro,
            'kwargs': {'interval': interval}
        })

    def get_handlers(self) -> List[Dict]:
        """Returns all registered handlers"""
        return self._handlers

    def get_loop_tasks(self) -> List[asyncio.Task]:
        """Returns active loop tasks"""
        return self._loop_tasks

    def add_loop_task(self, task: asyncio.Task):
        """Adds a loop task"""
        self._loop_tasks.append(task)

    def clear_loop_tasks(self):
        """Clears loop tasks"""
        for task in self._loop_tasks:
            if not task.done():
                task.cancel()
        self._loop_tasks.clear()

    @abstractmethod
    async def main(self) -> None:
        """Main plugin function"""
        pass


class PluginMetadata:
    """Class for storing plugin metadata"""

    def __init__(self, plugin_class, path: str = None, manifest: Dict = None):
        self.plugin_class = plugin_class
        self.path = path
        self.manifest = manifest or {}
        self.plugin_id = self.manifest.get('id', 'unknown')
        self.name = self.manifest.get('name', 'unnamed')
        self.author = self.manifest.get('author', 'unknown')
        self.version = self.manifest.get('version', '0.1')
        self.description = self.manifest.get('description', 'No description')
        self.category = self.manifest.get('category', 'General')
        self.requirements = self.manifest.get('requirements', [])
        self.instance = None
        self.folder_name = None

    def __repr__(self) -> str:
        return f"PluginMetadata(id='{self.plugin_id}', name='{self.name}', version='{self.version}')"
