#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
registry.py - Plugin and handler registry
"""

import asyncio
from typing import Dict, List, Optional, Callable, Any, Set
from collections import defaultdict
from dataclasses import dataclass, field
import logging


@dataclass
class HandlerInfo:
    """Information about a registered handler"""
    plugin_name: str
    handler_type: str  # command, watcher, callback, inline, loop
    handler: Callable
    kwargs: Dict[str, Any] = field(default_factory=dict)
    is_active: bool = True


class PluginRegistry:
    """
    Registry of all plugins and their handlers.
    
    Stores:
    - Loaded plugins
    - Commands
    - Watchers
    - Callbacks
    - Inline handlers
    - Loop tasks
    """
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialize()
        return cls._instance
    
    def _initialize(self):
        """Registry initialization"""
        self._plugins: Dict[str, any] = {}
        self._handlers: Dict[str, List[HandlerInfo]] = defaultdict(list)
        self._commands: Dict[str, HandlerInfo] = {}
        self._command_aliases: Dict[str, str] = {}
        self._watchers: List[HandlerInfo] = []
        self._callbacks: Dict[str, HandlerInfo] = {}
        self._inline_handlers: Dict[str, HandlerInfo] = {}
        self._loops: List[HandlerInfo] = []
        self._active_loop_tasks: Dict[str, asyncio.Task] = {}
        self._logger = logging.getLogger('PluginRegistry')
        self._localization = None
    
    def set_localization(self, localization):
        """Sets localization for the registry"""
        self._localization = localization
    
    def _t(self, key: str, **kwargs) -> str:
        """Gets a translation"""
        if self._localization:
            return self._localization.get(key, **kwargs)
        return key
    
    # ============ Plugins ============
    
    def register_plugin(self, plugin_metadata, plugin_name: Optional[str] = None) -> bool:
        """Registers a plugin in the registry.

        ``plugin_name`` is the folder name used by the loader and by every
        other registry lookup (handlers, pages, instances). The manifest name
        can differ from the folder name (for example ``testplugin`` ->
        ``TestPlugin``), so the folder name must be used as the key here too.
        Otherwise unload/reload cannot find and remove the old registration.
        """
        name = plugin_name or plugin_metadata.name
        if name in self._plugins:
            self._logger.warning(self._t("messages.plugins_already_registered_log", name=name))
            return False
        
        self._plugins[name] = plugin_metadata
        self._logger.info(self._t(
            "messages.plugins_registered_log",
            name=name,
            version=plugin_metadata.version
        ))
        return True
    
    def unregister_plugin(self, plugin_name: str) -> bool:
        """Removes a plugin from the registry."""
        if plugin_name not in self._plugins:
            return False
        
        self.remove_plugin_handlers(plugin_name)
        del self._plugins[plugin_name]
        self._logger.info(self._t("messages.plugins_unregistered_log", name=plugin_name))
        return True
    
    def get_plugin(self, plugin_name: str) -> Optional[any]:
        """Gets plugin metadata"""
        return self._plugins.get(plugin_name)
    
    def get_plugins(self) -> List[any]:
        """Gets a list of all registered plugins"""
        return list(self._plugins.values())
    
    def get_loaded_plugins(self) -> List[any]:
        """Gets a list of loaded plugins"""
        return [p for p in self._plugins.values() if p.instance and p.instance.is_loaded]
    
    def get_plugin_instance(self, plugin_name: str):
        """Gets the plugin instance"""
        metadata = self._plugins.get(plugin_name)
        if metadata:
            return metadata.instance
        return None
    
    # ============ Handlers ============
    
    def add_handler(self, plugin_name: str, handler_type: str, 
                    handler: Callable, **kwargs) -> None:
        """Adds a handler to the registry."""
        info = HandlerInfo(
            plugin_name=plugin_name,
            handler_type=handler_type,
            handler=handler,
            kwargs=kwargs
        )
        
        self._handlers[plugin_name].append(info)
        
        if handler_type == 'command':
            command_name = kwargs.get('command', handler.__name__)
            self._commands[command_name] = info
            for alias in kwargs.get('aliases', []):
                self._command_aliases[alias] = command_name
                
        elif handler_type == 'watcher':
            self._watchers.append(info)
            
        elif handler_type == 'callback':
            callback_id = kwargs.get('id', handler.__name__)
            self._callbacks[callback_id] = info
            
        elif handler_type == 'inline':
            inline_id = kwargs.get('id', handler.__name__)
            self._inline_handlers[inline_id] = info
            
        elif handler_type == 'loop':
            self._loops.append(info)
    
    def remove_plugin_handlers(self, plugin_name: str) -> None:
        """Removes all plugin handlers."""
        if plugin_name not in self._handlers:
            return
        
        for info in self._handlers[plugin_name]:
            if info.handler_type == 'loop':
                task_key = f"{plugin_name}:{info.handler.__name__}"
                if task_key in self._active_loop_tasks:
                    task = self._active_loop_tasks[task_key]
                    if not task.done():
                        task.cancel()
                    del self._active_loop_tasks[task_key]
        
        to_remove = []
        for cmd, info in self._commands.items():
            if info.plugin_name == plugin_name:
                to_remove.append(cmd)
        for cmd in to_remove:
            del self._commands[cmd]

        # Drop aliases that pointed at the removed commands, otherwise an
        # alias keeps resolving to a command that no longer exists.
        for alias, target in list(self._command_aliases.items()):
            if target in to_remove:
                del self._command_aliases[alias]
        
        self._watchers = [w for w in self._watchers if w.plugin_name != plugin_name]
        
        to_remove = []
        for cb_id, info in self._callbacks.items():
            if info.plugin_name == plugin_name:
                to_remove.append(cb_id)
        for cb_id in to_remove:
            del self._callbacks[cb_id]
        
        to_remove = []
        for inline_id, info in self._inline_handlers.items():
            if info.plugin_name == plugin_name:
                to_remove.append(inline_id)
        for inline_id in to_remove:
            del self._inline_handlers[inline_id]
        
        self._loops = [l for l in self._loops if l.plugin_name != plugin_name]
        del self._handlers[plugin_name]
        
        self._logger.debug(f"Removed handlers for plugin: {plugin_name}")
    
    def disable_handler(self, plugin_name: str, handler_name: str) -> bool:
        """Disables a handler."""
        if plugin_name not in self._handlers:
            return False
        
        for info in self._handlers[plugin_name]:
            if info.handler.__name__ == handler_name:
                info.is_active = False
                return True
        return False
    
    def enable_handler(self, plugin_name: str, handler_name: str) -> bool:
        """Enables a handler."""
        if plugin_name not in self._handlers:
            return False
        
        for info in self._handlers[plugin_name]:
            if info.handler.__name__ == handler_name:
                info.is_active = True
                return True
        return False
    
    # ============ Lookup ============
    
    def get_command(self, command_name: str) -> Optional[HandlerInfo]:
        """Gets the command handler."""
        if command_name in self._command_aliases:
            command_name = self._command_aliases[command_name]
        return self._commands.get(command_name)
    
    def get_commands(self) -> Dict[str, HandlerInfo]:
        """Gets all registered commands"""
        return self._commands.copy()
    
    def get_watchers(self) -> List[HandlerInfo]:
        """Gets all registered watchers"""
        return [w for w in self._watchers if w.is_active]
    
    def get_callback(self, callback_id: str) -> Optional[HandlerInfo]:
        """Gets the callback handler"""
        return self._callbacks.get(callback_id)
    
    def get_inline(self, inline_id: str) -> Optional[HandlerInfo]:
        """Gets the inline query handler"""
        return self._inline_handlers.get(inline_id)
    
    def get_loops(self) -> List[HandlerInfo]:
        """Gets all registered loop tasks"""
        return [l for l in self._loops if l.is_active]
    
    def get_plugin_handlers(self, plugin_name: str) -> List[HandlerInfo]:
        """Gets all plugin handlers"""
        return self._handlers.get(plugin_name, [])
    
    # ============ Loop tasks ============
    
    def add_loop_task(self, task_key: str, task: asyncio.Task) -> None:
        """Adds an active loop task."""
        self._active_loop_tasks[task_key] = task
    
    def remove_loop_task(self, task_key: str) -> None:
        """Removes a loop task."""
        if task_key in self._active_loop_tasks:
            task = self._active_loop_tasks[task_key]
            if not task.done():
                task.cancel()
            del self._active_loop_tasks[task_key]
    
    def get_loop_task(self, task_key: str) -> Optional[asyncio.Task]:
        """Gets a loop task by key"""
        return self._active_loop_tasks.get(task_key)
    
    def stop_all_loops(self) -> None:
        """Stops all active loop tasks"""
        for task_key, task in list(self._active_loop_tasks.items()):
            if not task.done():
                task.cancel()
            del self._active_loop_tasks[task_key]
