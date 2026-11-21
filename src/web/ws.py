#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WebSocket real-time log broadcasting."""

import asyncio
import logging
from datetime import datetime

from aiohttp import WSMsgType, web

from src.web import auth

MAX_WS_CLIENTS = 50
MAX_WS_QUEUE = 200


class LogBroadcaster:
    """Dispatches log events to connected WebSocket clients."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialize()
        return cls._instance

    def _initialize(self):
        self.clients: set[web.WebSocketResponse] = set()
        self.queues: dict[web.WebSocketResponse, asyncio.Queue] = {}
        self.loop: asyncio.AbstractEventLoop | None = None
        self._lock = asyncio.Lock()

    def set_loop(self, loop):
        self.loop = loop

    async def register(self, ws: web.WebSocketResponse) -> bool:
        async with self._lock:
            if len(self.clients) >= MAX_WS_CLIENTS:
                return False
            self.clients.add(ws)
            self.queues[ws] = asyncio.Queue(maxsize=MAX_WS_QUEUE)
            return True

    async def unregister(self, ws: web.WebSocketResponse):
        async with self._lock:
            self.clients.discard(ws)
            self.queues.pop(ws, None)

    async def close_all(self):
        async with self._lock:
            clients = list(self.clients)
            queues = list(self.queues.values())
            self.clients.clear()
            self.queues.clear()
        for q in queues:
            try:
                q.put_nowait(None)
            except Exception:
                pass
        for ws in clients:
            try:
                await ws.close()
            except Exception:
                pass

    def get_queue(self, ws: web.WebSocketResponse):
        return self.queues.get(ws)

    def broadcast(self, record: dict):
        loop = self.loop
        if not loop:
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            self._broadcast_now(record)
            return
        try:
            loop.call_soon_threadsafe(self._broadcast_now, record)
        except Exception:
            pass

    def _broadcast_now(self, record: dict):
        for ws, queue in list(self.queues.items()):
            if ws.closed:
                continue
            try:
                queue.put_nowait(record)
            except asyncio.QueueFull:
                # Drop oldest message to keep recent logs flowing for slow clients.
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    queue.put_nowait(record)
                except asyncio.QueueFull:
                    pass
            except Exception:
                continue


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


async def _ws_sender(ws: web.WebSocketResponse, queue: asyncio.Queue):
    try:
        while not ws.closed:
            record = await queue.get()
            if record is None:
                break
            try:
                await ws.send_json(record)
            except Exception:
                break
    except asyncio.CancelledError:
        pass
    except Exception:
        pass
    finally:
        if not ws.closed:
            try:
                await ws.close()
            except Exception:
                pass


async def websocket_logs(request):
    """WebSocket endpoint for streaming real-time logs."""
    origin = request.headers.get("Origin")
    if origin and not auth.is_trusted_origin(request):
        return web.json_response({"error": "invalid origin"}, status=403)
    if not origin:
        # Browsers normally send Origin on WS; reject suspicious cross-site attempts.
        # Allow only same-host requests with no Origin from non-browser tools if session is valid.
        # Session is already checked by auth middleware.
        pass

    ws = web.WebSocketResponse(heartbeat=30.0, max_msg_size=1024 * 1024)
    await ws.prepare(request)

    ensure_websocket_log_handler()
    broadcaster = LogBroadcaster()
    broadcaster.set_loop(asyncio.get_running_loop())
    registered = await broadcaster.register(ws)
    if not registered:
        await ws.close(code=1013, message=b"Too many connections")
        return ws

    queue = broadcaster.get_queue(ws)
    sender = asyncio.create_task(_ws_sender(ws, queue))

    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                if msg.data == "ping":
                    try:
                        await ws.send_str("pong")
                    except Exception:
                        break
            elif msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE, WSMsgType.CLOSING, WSMsgType.CLOSED):
                break
    except Exception:
        pass
    finally:
        await broadcaster.unregister(ws)
        if queue is not None:
            try:
                queue.put_nowait(None)
            except Exception:
                pass
        sender.cancel()
        try:
            await sender
        except Exception:
            pass

    return ws
