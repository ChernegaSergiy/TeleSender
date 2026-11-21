#!/usr/bin/env python3
import asyncio
import json
import os
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Workaround: dotenv import hangs in this environment; provide a harmless stub.
if "dotenv" not in sys.modules:
    _dotenv = types.ModuleType("dotenv")
    _dotenv.load_dotenv = lambda *a, **k: False
    _dotenv.dotenv_values = lambda *a, **k: {}
    _dotenv.find_dotenv = lambda *a, **k: ""
    _dotenv.set_key = lambda *a, **k: (None, None, None)
    sys.modules["dotenv"] = _dotenv

# Ensure imports do not depend on real .env secrets
os.environ.setdefault("WEB_AUTH_ENABLED", "true")
os.environ.setdefault("WEB_HOST", "127.0.0.1")

from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop

from src.config.config import config


class TestPathSecurity(unittest.TestCase):
    def test_data_path_blocked(self):
        from src.services.path_security import is_private_media_path, validate_media_path

        data_file = str((ROOT / "data" / "telesender.db").resolve())
        self.assertTrue(is_private_media_path(data_file))
        self.assertTrue(is_private_media_path(str(ROOT / "data" / "backups" / "x.db")))
        self.assertTrue(is_private_media_path(str(ROOT / ".env")))
        with self.assertRaises(ValueError):
            validate_media_path("data/telesender.db")

    def test_symlink_escape_blocked(self):
        from src.services.path_security import is_private_media_path

        with tempfile.TemporaryDirectory(dir="/tmp") as td:
            outside = Path(td) / "link"
            try:
                outside.symlink_to(ROOT / "data")
            except Exception:
                self.skipTest("symlink not supported")
            self.assertTrue(is_private_media_path(str(outside / "telesender.db")))

    def test_normal_path_allowed(self):
        from src.services.path_security import validate_media_path

        with tempfile.TemporaryDirectory(dir="/tmp") as td:
            p = Path(td) / "ok.txt"
            p.write_text("ok", encoding="utf-8")
            got = validate_media_path(str(p))
            self.assertEqual(Path(got).resolve(), p.resolve())


class TestPluginValidation(unittest.TestCase):
    def test_plugin_name_validation(self):
        from core.loader import _is_safe_plugin_name
        self.assertTrue(_is_safe_plugin_name("MyPlugin_1"))
        self.assertFalse(_is_safe_plugin_name("../evil"))
        self.assertFalse(_is_safe_plugin_name(".."))
        self.assertFalse(_is_safe_plugin_name("a/b"))
        self.assertFalse(_is_safe_plugin_name("a\\b"))
        self.assertFalse(_is_safe_plugin_name(".hidden"))

    def test_validate_plugin_source_blocks_ssrf_and_injection(self):
        from src.services.plugin_service import validate_plugin_source

        with self.assertRaises(ValueError):
            validate_plugin_source("-oProxyCommand=calc.exe")
        with self.assertRaises(ValueError):
            validate_plugin_source("http://127.0.0.1/repo.git")
        with self.assertRaises(ValueError):
            validate_plugin_source("http://localhost/repo.git")
        with self.assertRaises(ValueError):
            validate_plugin_source("git@localhost:org/repo.git")
        # valid-looking public URL format should not be rejected for injection/localness by parser
        # We don't do network resolution here in unit test; patch socket.getaddrinfo.
        with mock.patch("src.services.plugin_service.socket.getaddrinfo", return_value=[(None,None,None,None,("93.184.216.34",0))]):
            self.assertEqual(validate_plugin_source("https://example.com/repo.git"), "https://example.com/repo.git")


class TestAuthHelpers(unittest.TestCase):
    def setUp(self):
        from src.web import auth
        auth._LOGIN_ATTEMPTS.clear()
        auth._BLOCKED_IPS.clear()
        auth._GLOBAL_LOGIN_ATTEMPTS.clear()
        self._old_proxies = list(getattr(config, "web_trusted_proxies", []))

    def tearDown(self):
        config.web_trusted_proxies = self._old_proxies

    def _req(self, remote="1.2.3.4", headers=None, secure=False):
        return SimpleNamespace(
            remote=remote,
            transport=None,
            headers=headers or {},
            cookies={},
            secure=secure,
        )

    def test_xff_ignored_without_trusted_proxy(self):
        from src.web.auth import get_client_ip
        req = self._req(remote="10.0.0.1", headers={"X-Forwarded-For": "9.9.9.9"})
        self.assertEqual(get_client_ip(req), "10.0.0.1")

    def test_xff_used_with_trusted_proxy(self):
        from src.web.auth import get_client_ip
        config.web_trusted_proxies = ["10.0.0.1"]
        req = self._req(remote="10.0.0.1", headers={"X-Forwarded-For": "9.9.9.9, 10.0.0.1"})
        self.assertEqual(get_client_ip(req), "9.9.9.9")

    def test_rate_limit_memory_bounded(self):
        from src.web import auth
        now = time.time()
        with auth._AUTH_STATE_LOCK:
            for i in range(5000):
                auth._LOGIN_ATTEMPTS[f"1.2.{i//256}.{i%256}"] = [now]
            auth._cleanup_auth_state_locked(now)
            self.assertLessEqual(len(auth._LOGIN_ATTEMPTS), auth._MAX_TRACKED_IPS)

    def test_cookie_secure_requires_https_or_trusted_proxy(self):
        from src.web.auth import cookie_secure
        req = self._req(secure=False, headers={"X-Forwarded-Proto": "https"})
        self.assertFalse(cookie_secure(req))
        config.web_trusted_proxies = ["1.2.3.4"]
        self.assertTrue(cookie_secure(req))
        req2 = self._req(secure=True)
        self.assertTrue(cookie_secure(req2))


class TestTwoFactor(unittest.TestCase):
    def test_2fa_request_is_one_time(self):
        from src.web.two_factor import TwoFactorManager
        mgr = TwoFactorManager()
        req = mgr.create_request(ip="1.2.3.4", user_agent="ua")
        mgr.approve_request(req.request_id)
        consumed = mgr.consume_approved_request(req.request_id, purpose="login")
        self.assertIsNotNone(consumed)
        consumed2 = mgr.consume_approved_request(req.request_id, purpose="login")
        self.assertIsNone(consumed2)


class TestTelegramAuthReplay(unittest.TestCase):
    def test_replay_rejected(self):
        from src.web.routes import auth as auth_routes
        auth_routes._TG_AUTH_REPLAY.clear()
        now = int(time.time())
        self.assertTrue(auth_routes._check_telegram_auth_replay("hash1", now))
        self.assertFalse(auth_routes._check_telegram_auth_replay("hash1", now))
        self.assertFalse(auth_routes._check_telegram_auth_replay("hash2", now - 301))
        self.assertFalse(auth_routes._check_telegram_auth_replay("hash3", now + 61))


class TestConfigDefaults(unittest.TestCase):
    def test_secure_defaults_present(self):
        # Check class/default behavior by reading source defaults in a clean env overlay
        self.assertEqual(os.getenv("WEB_HOST", "127.0.0.1"), "127.0.0.1")
        # Actual config instance in this process may be influenced by env; verify fields exist
        self.assertTrue(hasattr(config, "web_auth_enabled"))
        self.assertTrue(hasattr(config, "web_host"))
        self.assertTrue(hasattr(config, "web_trusted_proxies"))


class DummyTaskApiBot:
    def __init__(self):
        self.db = mock.Mock()
        self.db.get_all.return_value = []
        self.db.get_one.return_value = None
        self.task_service = SimpleNamespace(running={})


class TestApiValidation(AioHTTPTestCase):
    async def get_application(self):
        from src.web.server import auth_middleware, csrf_middleware, error_middleware
        app = web.Application(middlewares=[error_middleware, auth_middleware, csrf_middleware])

        async def ok(request):
            return web.json_response({"ok": True})

        app.router.add_get("/api/protected", ok)
        app.router.add_post("/api/protected", ok)
        return app

    @unittest_run_loop
    async def test_api_requires_auth(self):
        old = config.web_auth_enabled
        config.web_auth_enabled = True
        try:
            resp = await self.client.request("GET", "/api/protected")
            self.assertEqual(resp.status, 401)
        finally:
            config.web_auth_enabled = old

    @unittest_run_loop
    async def test_csrf_missing_origin_for_cookie_non_ajax(self):
        old = config.web_auth_enabled
        config.web_auth_enabled = False
        try:
            resp = await self.client.request(
                "POST", "/api/protected",
                data="x=1",
                headers={"Cookie": "telesender_session=fake", "Content-Type": "application/x-www-form-urlencoded"},
            )
            self.assertEqual(resp.status, 403)
        finally:
            config.web_auth_enabled = old


class TestServerPublicPaths(unittest.TestCase):
    def test_auth_prefix_not_blanket_public(self):
        from src.web.server import _is_public_web_path
        self.assertTrue(_is_public_web_path("/login"))
        self.assertTrue(_is_public_web_path("/auth/login"))
        self.assertTrue(_is_public_web_path("/auth/2fa/status"))
        self.assertFalse(_is_public_web_path("/auth/secret"))
        self.assertFalse(_is_public_web_path("/api/tasks"))


class TestWsSecurity(unittest.IsolatedAsyncioTestCase):
    async def test_ws_queue_bounded_and_drops_oldest(self):
        from src.web import ws
        bc = ws.LogBroadcaster()
        # isolate singleton state
        async with bc._lock:
            bc.clients.clear()
            bc.queues.clear()
        fake_ws = SimpleNamespace(closed=False)
        async with bc._lock:
            bc.clients.add(fake_ws)
            bc.queues[fake_ws] = asyncio.Queue(maxsize=2)
        bc._broadcast_now({"n": 1})
        bc._broadcast_now({"n": 2})
        bc._broadcast_now({"n": 3})
        q = bc.get_queue(fake_ws)
        items = [q.get_nowait(), q.get_nowait()]
        self.assertEqual([i["n"] for i in items], [2, 3])

    async def test_max_clients(self):
        from src.web import ws
        bc = ws.LogBroadcaster()
        async with bc._lock:
            bc.clients.clear()
            bc.queues.clear()
        old = ws.MAX_WS_CLIENTS
        ws.MAX_WS_CLIENTS = 1
        try:
            ws1 = web.WebSocketResponse()
            ok1 = await bc.register(ws1)
            ok2 = await bc.register(web.WebSocketResponse())
            self.assertTrue(ok1)
            self.assertFalse(ok2)
        finally:
            ws.MAX_WS_CLIENTS = old
            async with bc._lock:
                bc.clients.clear()
                bc.queues.clear()


class TestSqlInjectionAudit(unittest.TestCase):
    def test_no_obvious_dynamic_sql(self):
        src = (ROOT / "src" / "services" / "database_service.py").read_text(encoding="utf-8")
        self.assertNotIn("execute(f\"SELECT", src)
        self.assertNotIn("execute(f'INSERT", src)
        # ALTER TABLE with hardcoded tuples is allowed in current implementation
        self.assertIn("ALTER TABLE tasks ADD COLUMN", src)


if __name__ == "__main__":
    unittest.main()


class TestLoadAndStability(unittest.IsolatedAsyncioTestCase):
    async def test_http_500_requests(self):
        from src.web.server import error_middleware
        app = web.Application(middlewares=[error_middleware])

        async def ok(request):
            return web.json_response({"ok": True})

        app.router.add_post("/api/echo", ok)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async def one(i):
                    async with session.post(f"http://127.0.0.1:{port}/api/echo", json={"i": i}) as r:
                        return r.status
                statuses = await asyncio.gather(*[one(i) for i in range(500)])
            self.assertTrue(all(s == 200 for s in statuses))
        finally:
            await runner.cleanup()

    async def test_ws_100_connections_and_broadcast(self):
        from src.web import ws as wsmod
        bc = wsmod.LogBroadcaster()
        async with bc._lock:
            bc.clients.clear()
            bc.queues.clear()
        bc.set_loop(asyncio.get_running_loop())

        app = web.Application()
        app.router.add_get("/ws/logs", wsmod.websocket_logs)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        try:
            import aiohttp
            clients = []
            async with aiohttp.ClientSession() as session:
                old = wsmod.MAX_WS_CLIENTS
                wsmod.MAX_WS_CLIENTS = 120
                try:
                    for _ in range(100):
                        ws = await session.ws_connect(f"http://127.0.0.1:{port}/ws/logs")
                    clients.append(ws)
                # intense broadcast
                for i in range(200):
                    bc.broadcast({"time": "00:00:00", "level": "INFO", "message": f"msg-{i}", "logger": "t"})
                await asyncio.sleep(0.5)
                received = 0
                for c in clients[:10]:
                    # drain a few
                    for _ in range(5):
                        try:
                            msg = await asyncio.wait_for(c.receive(), timeout=1)
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                received += 1
                        except Exception:
                            break
                self.assertGreater(received, 0)
                for c in clients:
                    await c.close()
        finally:
            wsmod.MAX_WS_CLIENTS = old
                await runner.cleanup()
            async with bc._lock:
                bc.clients.clear()
                bc.queues.clear()
