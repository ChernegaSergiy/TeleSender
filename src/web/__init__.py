#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Web package exports."""

__all__ = ["Server", "WebServerManager", "start_web_server"]


def __getattr__(name):
    if name in __all__:
        from src.web import server as _server
        return getattr(_server, name)
    raise AttributeError(name)
