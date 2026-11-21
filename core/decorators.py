#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
decorators.py - Plugin decorators
"""

import asyncio
from typing import Callable, List, Optional, Union, Any
from functools import wraps
import logging


logger = logging.getLogger('decorators')


class CommandDecorator:
    """Decorator for registering commands"""

    _pending_handlers = []

    @classmethod
    def register(cls, command: Optional[str] = None,
                 aliases: Optional[List[str]] = None,
                 description: str = "",
                 usage: str = "",
                 hide: bool = False) -> Callable:
        """
        Decorator for registering a command.

        Args:
            command: Command name (if None, the function name is used)
            aliases: List of aliases
            description: Command description
            usage: Usage example
            hide: Hide the command from /help

        Returns:
            Decorator
        """
        def decorator(func: Callable) -> Callable:
            cmd_name = command or func.__name__

            @wraps(func)
            async def wrapper(*args, **kwargs):
                return await func(*args, **kwargs)

            # Save the metadata
            wrapper._is_command = True
            wrapper._command_name = cmd_name
            wrapper._command_aliases = aliases or []
            wrapper._command_description = description
            wrapper._command_usage = usage
            wrapper._command_hide = hide

            cls._pending_handlers.append({
                'type': 'command',
                'handler': wrapper,
                'kwargs': {
                    'command': cmd_name,
                    'aliases': aliases or [],
                    'description': description,
                    'usage': usage,
                    'hide': hide
                }
            })

            return wrapper
        return decorator


class WatcherDecorator:
    """Decorator for registering watchers"""

    _pending_handlers = []

    @classmethod
    def register(cls, pattern: Optional[str] = None,
                 regex: bool = False,
                 only_private: bool = False,
                 only_groups: bool = False,
                 from_users: Optional[List[int]] = None) -> Callable:
        """
        Decorator for registering a watcher.

        Args:
            pattern: Search pattern
            regex: Use a regular expression
            only_private: Private chats only
            only_groups: Groups only
            from_users: List of user IDs

        Returns:
            Decorator
        """
        def decorator(func: Callable) -> Callable:
            @wraps(func)
            async def wrapper(*args, **kwargs):
                return await func(*args, **kwargs)

            wrapper._is_watcher = True
            wrapper._watcher_pattern = pattern
            wrapper._watcher_regex = regex
            wrapper._watcher_only_private = only_private
            wrapper._watcher_only_groups = only_groups
            wrapper._watcher_from_users = from_users or []

            cls._pending_handlers.append({
                'type': 'watcher',
                'handler': wrapper,
                'kwargs': {
                    'pattern': pattern,
                    'regex': regex,
                    'only_private': only_private,
                    'only_groups': only_groups,
                    'from_users': from_users or []
                }
            })

            return wrapper
        return decorator


class CallbackDecorator:
    """Decorator for registering callback handlers"""

    _pending_handlers = []

    @classmethod
    def register(cls, callback_id: Optional[str] = None) -> Callable:
        """
        Decorator for registering a callback handler.

        Args:
            callback_id: Callback ID (if None, the function name is used)

        Returns:
            Decorator
        """
        def decorator(func: Callable) -> Callable:
            cb_id = callback_id or func.__name__

            @wraps(func)
            async def wrapper(*args, **kwargs):
                return await func(*args, **kwargs)

            wrapper._is_callback = True
            wrapper._callback_id = cb_id

            cls._pending_handlers.append({
                'type': 'callback',
                'handler': wrapper,
                'kwargs': {
                    'id': cb_id
                }
            })

            return wrapper
        return decorator


class InlineDecorator:
    """Decorator for registering inline handlers"""

    _pending_handlers = []

    @classmethod
    def register(cls, inline_id: Optional[str] = None) -> Callable:
        """
        Decorator for registering an inline handler.

        Args:
            inline_id: Inline query ID (if None, the function name is used)

        Returns:
            Decorator
        """
        def decorator(func: Callable) -> Callable:
            resolved_id = inline_id or func.__name__

            @wraps(func)
            async def wrapper(*args, **kwargs):
                return await func(*args, **kwargs)

            wrapper._is_inline = True
            wrapper._inline_id = resolved_id

            cls._pending_handlers.append({
                'type': 'inline',
                'handler': wrapper,
                'kwargs': {
                    'id': resolved_id
                }
            })

            return wrapper
        return decorator


class LoopDecorator:
    """Decorator for registering loop tasks"""

    _pending_handlers = []

    @classmethod
    def register(cls, interval: float = 60.0, 
                 start_immediately: bool = False) -> Callable:
        """
        Decorator for registering a loop task.

        Args:
            interval: Interval in seconds
            start_immediately: Start immediately

        Returns:
            Decorator
        """
        def decorator(func: Callable) -> Callable:
            @wraps(func)
            async def wrapper(*args, **kwargs):
                return await func(*args, **kwargs)

            wrapper._is_loop = True
            wrapper._loop_interval = interval
            wrapper._loop_start_immediately = start_immediately

            cls._pending_handlers.append({
                'type': 'loop',
                'handler': wrapper,
                'kwargs': {
                    'interval': interval,
                    'start_immediately': start_immediately
                }
            })

            return wrapper
        return decorator


# Convenient aliases
def command(command: Optional[str] = None, 
            aliases: Optional[List[str]] = None,
            description: str = "",
            usage: str = "",
            hide: bool = False) -> Callable:
    """Alias for CommandDecorator.register"""
    return CommandDecorator.register(command, aliases, description, usage, hide)


def watcher(pattern: Optional[str] = None,
            regex: bool = False,
            only_private: bool = False,
            only_groups: bool = False,
            from_users: Optional[List[int]] = None) -> Callable:
    """Alias for WatcherDecorator.register"""
    return WatcherDecorator.register(pattern, regex, only_private, only_groups, from_users)


def callback(callback_id: Optional[str] = None) -> Callable:
    """Alias for CallbackDecorator.register"""
    return CallbackDecorator.register(callback_id)


def inline(inline_id: Optional[str] = None) -> Callable:
    """Alias for InlineDecorator.register"""
    return InlineDecorator.register(inline_id)


def loop(interval: float = 60.0, start_immediately: bool = False) -> Callable:
    """Alias for LoopDecorator.register"""
    return LoopDecorator.register(interval, start_immediately)


def get_pending_handlers() -> List[dict]:
    """
    Gets all handlers registered by decorators.

    Returns:
        List of handler dictionaries
    """
    handlers = []
    handlers.extend(CommandDecorator._pending_handlers)
    handlers.extend(WatcherDecorator._pending_handlers)
    handlers.extend(CallbackDecorator._pending_handlers)
    handlers.extend(InlineDecorator._pending_handlers)
    handlers.extend(LoopDecorator._pending_handlers)

    # Clear the queues
    CommandDecorator._pending_handlers.clear()
    WatcherDecorator._pending_handlers.clear()
    CallbackDecorator._pending_handlers.clear()
    InlineDecorator._pending_handlers.clear()
    LoopDecorator._pending_handlers.clear()

    return handlers


def clear_pending_handlers():
    """Clears all decorator queues"""
    CommandDecorator._pending_handlers.clear()
    WatcherDecorator._pending_handlers.clear()
    CallbackDecorator._pending_handlers.clear()
    InlineDecorator._pending_handlers.clear()
    LoopDecorator._pending_handlers.clear()
