#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WebSocket real-time log broadcasting."""

import asyncio
import logging
from datetime import datetime
from aiohttp import WSMsgType, web


class LogBroadcaster:
    """Dispatches log events to connected WebSocket clients."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialize()
        return cls._instance

    def _initialize(self):
        self.clients = set()
        self.loop = None

    def set_loop(self, loop):
        self.loop = loop

    async def register(self, ws):
        self.clients.add(ws)

    async def unregister(self, ws):
        self.clients.discard(ws)

    def broadcast(self, record: dict):
        if not self.clients or not self.loop:
            return

        for ws in list(self.clients):
            try:
                asyncio.run_coroutine_threadsafe(ws.send_json(record), self.loop)
            except Exception:
                pass


class WebSocketLogHandler(logging.Handler):
    """Logging handler streaming records to WebSocket."""

    def __init__(self):
        super().__init__()
        self.broadcaster = LogBroadcaster()

    def emit(self, record):
        try:
            log_entry = {
                "time": datetime.fromtimestamp(record.created).strftime("%H:%M:%S"),
                "level": record.levelname,
                "message": record.getMessage(),
                "logger": record.name,
            }
            self.broadcaster.broadcast(log_entry)
        except Exception:
            pass


def ensure_websocket_log_handler() -> WebSocketLogHandler:
    """Attaches the WebSocket log handler to the root logger once."""
    root_logger = logging.getLogger()
    for handler in root_logger.handlers:
        if isinstance(handler, WebSocketLogHandler):
            return handler

    handler = WebSocketLogHandler()
    handler.setLevel(logging.DEBUG)
    root_logger.addHandler(handler)
    return handler


async def websocket_logs(request):
    """WebSocket endpoint for streaming real-time logs."""
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    ensure_websocket_log_handler()
    broadcaster = LogBroadcaster()
    broadcaster.set_loop(asyncio.get_running_loop())
    await broadcaster.register(ws)

    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                if msg.data == "ping":
                    await ws.send_str("pong")
            elif msg.type == WSMsgType.ERROR:
                break
    finally:
        await broadcaster.unregister(ws)

    return ws
