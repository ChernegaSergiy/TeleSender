#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
loader.py - Plugin loader
"""

import os
import sys
import importlib
import importlib.util
import inspect
import asyncio
import json
import re
from typing import Dict, List, Optional, Type, Any, Callable
from pathlib import Path
import logging

from .plugin import Plugin, PluginMetadata, generate_plugin_id
from .registry import PluginRegistry
from .decorators import get_pending_handlers, clear_pending_handlers
from .localization import PluginLocalization, CoreLocalization
from .pages import PageRegistry, collect_page_definitions


class PluginLoader:
    """
    Plugin loader.
    
    Responsible for:
    - Finding plugins in the plugins/ folder
    - Loading and unloading plugins
    - Hot reload
    - Handler registration
    """
    
    def __init__(self, client=None, db=None, bot=None, plugins_dir: str = 'plugins'):
        """
        Loader initialization.
        
        Args:
            client: TelegramClient
            db: Database
            bot: AutoSender instance
            plugins_dir: Path to the plugins folder
        """
        self.client = client
        self.db = db
        self.bot = bot
        self.inline = None  # InlineManager — set via set_inline()
        self.plugins_dir = plugins_dir
        self.registry = PluginRegistry()
        self.pages = PageRegistry()
        self.core_localization = CoreLocalization()
        self.logger = logging.getLogger('PluginLoader')
        self._loaded_modules: Dict[str, any] = {}
        self._instances: Dict[str, Plugin] = {}
        self._loading = False
        
        # Pass localization to the registry
        self.registry.set_localization(self.core_localization)
        
        if not os.path.exists(plugins_dir):
            os.makedirs(plugins_dir)
        
        self._init_config_table()
    
    def _t(self, key: str, **kwargs) -> str:
        """Gets a translation"""
        return self.core_localization.get(key, **kwargs)
    
    def _init_config_table(self):
        """Creates the plugin configuration table"""
        if not self.db:
            return
        
        try:
            cursor = self.db.cursor
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS plugin_config (
                    id TEXT PRIMARY KEY,
                    config TEXT,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            self.db.conn.commit()
        except Exception as e:
            self.logger.warning(f"Failed to create plugin_config table: {e}")
    
    def set_client(self, client):
        """Sets the TelegramClient"""
        self.client = client
    
    def set_db(self, db):
        """Sets the database"""
        self.db = db
        self._init_config_table()
    
    def set_bot(self, bot):
        """Sets the bot"""
        self.bot = bot

    def set_inline(self, inline_manager):
        """Sets the InlineManager for plugin injection."""
        self.inline = inline_manager
        # Update already-loaded plugin instances
        for plugin_name, inst in self._instances.items():
            inst.inline = inline_manager
    
    PLUGIN_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")

    def _is_safe_plugin_name(self, plugin_name: str) -> bool:
        return isinstance(plugin_name, str) and bool(self.PLUGIN_NAME_RE.fullmatch(plugin_name))

    def _resolve_plugin_path(self, plugin_name: str) -> Optional[Path]:
        """Resolves plugin directory and ensures it stays inside plugins_dir."""
        if not self._is_safe_plugin_name(plugin_name):
            return None
        base = Path(self.plugins_dir).resolve(strict=False)
        candidate = (base / plugin_name).resolve(strict=False)
        if candidate == base:
            return None
        try:
            candidate.relative_to(base)
        except Exception:
            return None
        # Reject symlink escapes: the real target must also stay inside plugins_dir.
        try:
            real_base = base.resolve()
            real_candidate = candidate.resolve()
            real_candidate.relative_to(real_base)
        except Exception:
            return None
        return candidate

    def find_plugins(self) -> List[str]:
        """
        Finds all plugins in the folder.
        
        Returns:
            List of plugin names
        """
        plugins = []
        if not os.path.exists(self.plugins_dir):
            return plugins
        
        for item in os.listdir(self.plugins_dir):
            if not self._is_safe_plugin_name(item):
                continue
            plugin_path = self._resolve_plugin_path(item)
            if not plugin_path or not plugin_path.is_dir():
                continue
            plugin_file = plugin_path / 'plugin.py'
            manifest_file = plugin_path / 'manifest.json'
            if plugin_file.exists() and manifest_file.exists():
                plugins.append(item)
        
        return plugins
    
    def _load_manifest(self, plugin_path: str) -> Optional[Dict]:
        """Loads the plugin manifest"""
        manifest_path = os.path.join(plugin_path, 'manifest.json')
        if not os.path.exists(manifest_path):
            self.logger.error(self._t(
                "messages.plugins_manifest_not_found_log",
                path=plugin_path
            ))
            return None
        
        try:
            with open(manifest_path, 'r', encoding='utf-8') as f:
                manifest = json.load(f)
            
            # Validating required fields
            required = ['name', 'author', 'version']
            missing = [f for f in required if f not in manifest]
            if missing:
                self.logger.error(self._t(
                    "messages.plugins_manifest_missing_fields",
                    fields=', '.join(missing)
                ))
                return None
            
            # Check or generate the ID
            if 'id' not in manifest:
                manifest['id'] = generate_plugin_id(manifest['name'], manifest['author'])
                self.logger.warning(self._t(
                    "messages.plugins_id_generated",
                    id=manifest['id']
                ))
            else:
                expected_id = generate_plugin_id(manifest['name'], manifest['author'])
                if manifest['id'] != expected_id:
                    self.logger.warning(self._t(
                        "messages.plugins_id_mismatch_log",
                        id=manifest['id'],
                        expected=expected_id
                    ))
            
            return manifest
        except Exception as e:
            self.logger.error(self._t(
                "messages.plugins_failed_load",
                name='manifest',
                error=str(e)
            ))
            return None
    
    def _load_config(self, plugin_path: str) -> Dict:
        """Loads configuration from config.json"""
        config_path = os.path.join(plugin_path, 'config.json')
        if not os.path.exists(config_path):
            return {}
        
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            self.logger.error(f"Failed to load config: {e}")
            return {}
    
    async def load_plugin(self, plugin_name: str) -> bool:
        """
        Loads a plugin.
        
        Args:
            plugin_name: Plugin name
        
        Returns:
            True on success
        """
        if self._loading:
            self.logger.warning(self._t("messages.plugins_loader_busy"))
            return False
        
        self._loading = True
        
        try:
            # Check whether it is already loaded
            if plugin_name in self._loaded_modules:
                self.logger.warning(self._t(
                    "messages.plugins_already_loaded_log",
                    name=plugin_name
                ))
                return False
            
            safe_path = self._resolve_plugin_path(plugin_name)
            if not safe_path:
                self.logger.error(f"Rejected unsafe plugin name: {plugin_name!r}")
                return False

            plugin_path = str(safe_path)
            plugin_file = os.path.join(plugin_path, 'plugin.py')
            plugin_file_resolved = Path(plugin_file).resolve(strict=False)
            try:
                plugin_file_resolved.relative_to(Path(self.plugins_dir).resolve(strict=False))
            except Exception:
                self.logger.error(f"Rejected plugin file outside plugins directory: {plugin_name!r}")
                return False

            if not os.path.isdir(plugin_path) or not os.path.exists(plugin_file):
                self.logger.error(self._t(
                    "messages.plugins_file_not_found",
                    path=plugin_file
                ))
                return False
            
            # Load the manifest
            manifest = self._load_manifest(plugin_path)
            if not manifest:
                return False
            
            # Load the config
            config = self._load_config(plugin_path)
            
            # Add the import path
            if plugin_path not in sys.path:
                sys.path.insert(0, plugin_path)
            
            # Import the module
            spec = importlib.util.spec_from_file_location(
                f"plugins.{plugin_name}",
                plugin_file
            )
            if not spec or not spec.loader:
                self.logger.error(self._t(
                    "messages.plugins_spec_error",
                    name=plugin_name
                ))
                return False
            
            module = importlib.util.module_from_spec(spec)
            sys.modules[f"plugins.{plugin_name}"] = module
            spec.loader.exec_module(module)
            
            # Look for the Plugin class
            plugin_class = None
            for name, obj in inspect.getmembers(module):
                if (inspect.isclass(obj) and 
                    issubclass(obj, Plugin) and 
                    obj is not Plugin):
                    plugin_class = obj
                    break
            
            if not plugin_class:
                self.logger.error(self._t(
                    "messages.plugins_no_class",
                    name=plugin_name
                ))
                return False
            
            # Create a plugin instance with the manifest and config
            plugin_instance = plugin_class(
                client=self.client,
                db=self.db,
                bot=self.bot,
                manifest=manifest,
                config=config
            )
            plugin_instance.path = plugin_path
            plugin_instance.folder_name = plugin_name
            
            # Inject inline manager
            plugin_instance.inline = self.inline
            
            # Store the instance
            self._instances[plugin_name] = plugin_instance
            
            # Set up localization
            lang_dir = os.path.join(plugin_path, 'lang')
            if os.path.exists(lang_dir):
                localization = PluginLocalization(
                    plugin_name=plugin_name,
                    lang_dir=lang_dir,
                    default_lang=self.core_localization.current_lang
                )
                localization.set_language(self.core_localization.current_lang)
                plugin_instance.set_localization(localization)
            
            # Get handlers registered by decorators
            pending = get_pending_handlers()

            self.logger.info(self._t(
                "messages.plugins_handlers_found",
                count=len(pending),
                name=plugin_name
            ))
            
            # Register the handlers
            for handler_info in pending:
                self.registry.add_handler(
                    plugin_name=plugin_name,
                    handler_type=handler_info['type'],
                    handler=handler_info['handler'],
                    **handler_info['kwargs']
                )
                plugin_instance.register_handler(
                    handler_type=handler_info['type'],
                    handler=handler_info['handler'],
                    **handler_info['kwargs']
                )
            
            # Clear the decorator queue
            clear_pending_handlers()
            
            # Save the metadata. The registry is keyed by the folder name so
            # that unload/reload removes the handlers registered above, even
            # when the manifest name differs from the folder name.
            metadata = PluginMetadata(plugin_class, plugin_path, manifest)
            metadata.instance = plugin_instance
            metadata.folder_name = plugin_name
            
            self.registry.register_plugin(metadata, plugin_name=plugin_name)
            self._loaded_modules[plugin_name] = module
            
            # Call on_load
            await plugin_instance.on_load()
            
            # Run main
            await plugin_instance.main()
            
            # Start the registered loops
            await self._start_loops(plugin_name, plugin_instance)
            
            # Set the load status and register web pages
            plugin_instance._loaded = True
            self._register_pages(plugin_name, plugin_instance)
            
            self.logger.info(self._t(
                "messages.plugins_loaded_success",
                name=plugin_name,
                version=manifest['version']
            ))
            self.logger.info(f"    ID: {manifest['id']}")
            return True
            
        except Exception as e:
            self.logger.exception(self._t(
                "messages.plugins_failed_load",
                name=plugin_name,
                error=str(e)
            ))
            return False
        finally:
            self._loading = False
    
    def _register_pages(self, plugin_name: str, plugin_instance: Plugin) -> None:
        """Registers all @page methods exposed by a loaded plugin."""
        for page_definition in collect_page_definitions(plugin_name, plugin_instance):
            self.pages.register(page_definition)

    async def _start_loops(self, plugin_name: str, plugin_instance: Plugin):
        """
        Starts the registered loop tasks.
        
        Args:
            plugin_name: Plugin name
            plugin_instance: Plugin instance
        """
        handlers = self.registry.get_plugin_handlers(plugin_name)
        for info in handlers:
            if info.handler_type == 'loop' and info.is_active:
                task_key = f"{plugin_name}:{info.handler.__name__}"
                
                async def loop_wrapper():
                    interval = info.kwargs.get('interval', 60.0)
                    start_immediately = info.kwargs.get('start_immediately', False)
                    
                    if start_immediately:
                        try:
                            await info.handler(plugin_instance)
                        except Exception as e:
                            self.logger.error(f"Loop {info.handler.__name__} error: {e}")
                    
                    while True:
                        await asyncio.sleep(interval)
                        try:
                            await info.handler(plugin_instance)
                        except asyncio.CancelledError:
                            break
                        except Exception as e:
                            self.logger.error(f"Loop {info.handler.__name__} error: {e}")
                
                task = asyncio.create_task(loop_wrapper())
                self.registry.add_loop_task(task_key, task)
                plugin_instance.add_loop_task(task)
    
    async def unload_plugin(self, plugin_name: str) -> bool:
        """
        Unloads a plugin.
        
        Args:
            plugin_name: Plugin name
        
        Returns:
            True on success
        """
        if plugin_name not in self._loaded_modules:
            self.logger.warning(self._t(
                "messages.plugins_already_loaded_log",
                name=plugin_name
            ))
            return False
        
        try:
            # Get the instance
            metadata = self.registry.get_plugin(plugin_name)
            if metadata and metadata.instance:
                # Call on_unload
                await metadata.instance.on_unload()
                # Stop the loops
                metadata.instance.clear_loop_tasks()
            
            # Remove web pages and the registry entry
            self.pages.unregister_plugin(plugin_name)
            self.registry.unregister_plugin(plugin_name)
            
            # Remove from modules
            if plugin_name in self._loaded_modules:
                del self._loaded_modules[plugin_name]
            
            # Remove from instances
            if plugin_name in self._instances:
                del self._instances[plugin_name]
            
            # Remove from sys.modules
            module_name = f"plugins.{plugin_name}"
            if module_name in sys.modules:
                del sys.modules[module_name]
            
            self.logger.info(self._t(
                "messages.plugins_unloaded_success",
                name=plugin_name
            ))
            return True
            
        except Exception as e:
            self.logger.error(self._t(
                "messages.plugins_failed_unload",
                name=plugin_name,
                error=str(e)
            ))
            return False
    
    async def reload_plugin(self, plugin_name: str) -> bool:
        """
        Reloads a plugin (hot reload).
        
        Args:
            plugin_name: Plugin name
        
        Returns:
            True on success
        """
        self.logger.info(f"Reloading plugin: {plugin_name}")
        
        # Unloading
        if not await self.unload_plugin(plugin_name):
            return False
        
        # Loading
        return await self.load_plugin(plugin_name)
    
    async def load_all_plugins(self) -> int:
        """
        Loads all discovered plugins.
        
        Returns:
            Number of loaded plugins
        """
        plugins = self.find_plugins()
        loaded = 0
        
        for plugin_name in plugins:
            if await self.load_plugin(plugin_name):
                loaded += 1
        
        self.logger.info(self._t(
            "messages.plugins_loaded_count",
            loaded=loaded,
            total=len(plugins)
        ))
        return loaded
    
    async def unload_all_plugins(self) -> int:
        """
        Unloads all plugins.
        
        Returns:
            Number of unloaded plugins
        """
        plugins = list(self._loaded_modules.keys())
        unloaded = 0
        
        for plugin_name in plugins:
            if await self.unload_plugin(plugin_name):
                unloaded += 1
        
        self.logger.info(self._t(
            "messages.plugins_unloaded_count",
            unloaded=unloaded,
            total=len(plugins)
        ))
        return unloaded
    
    def get_loaded_plugins(self) -> List[str]:
        """
        Gets a list of loaded plugins.
        
        Returns:
            List of plugin names
        """
        return list(self._loaded_modules.keys())
    
    def get_plugin_instance(self, plugin_name: str):
        """
        Gets the plugin instance.
        
        Args:
            plugin_name: Plugin name
        
        Returns:
            Plugin instance or None
        """
        # First check the instance dictionary
        if plugin_name in self._instances:
            return self._instances[plugin_name]
        
        # Then in the registry
        metadata = self.registry.get_plugin(plugin_name)
        if metadata:
            return metadata.instance
        
        return None
    
    def set_language(self, lang_code: str) -> bool:
        """
        Changes the language for all plugins.
        
        Args:
            lang_code: Language code
        
        Returns:
            True on success
        """
        # Change the core language
        if not self.core_localization.set_language(lang_code):
            return False

        # Change the language for all loaded plugins. Iterate the instances
        # directly: the registry is keyed by the manifest name, which often
        # differs from the plugin's folder name (e.g. gemini -> GeminiAI).
        for plugin_name, instance in self._instances.items():
            localization = getattr(instance, '_localization', None)
            if not localization:
                continue
            try:
                localization.set_language(lang_code)
            except Exception:
                self.logger.exception(
                    "Failed to change language for plugin %s", plugin_name
                )

        self.logger.info(self._t("messages.plugins_language_changed", lang=lang_code))
        return True
    
    def get_core_localization(self) -> CoreLocalization:
        """Gets core localization"""
        return self.core_localization
