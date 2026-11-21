#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core - Plugin system for UserBot
"""

from .plugin import Plugin
from .loader import PluginLoader
from .registry import PluginRegistry
from .decorators import command, watcher, callback, inline, loop
from .pages import PluginPages, PageRegistry, PageDefinition, page
from .localization import PluginLocalization

__all__ = [
    'Plugin',
    'PluginLoader',
    'PluginRegistry',
    'command',
    'watcher',
    'callback',
    'inline',
    'loop',
    'page',
    'PluginPages',
    'PageRegistry',
    'PageDefinition',
    'PluginLocalization'
]
