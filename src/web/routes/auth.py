#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Authentication routes including 2FA via Telegram inline bot."""

import hashlib
import hmac
import time
from aiohttp import web
import aiohttp_jinja2

from src.config.config import config
from src.services.logger import log
from src.services.translator import lang
from src.web import auth
from src.web.sessions import consume_bot_login_code


# High-entropy User-Agent Client Hints. Chromium only sends these over HTTPS
# and only after a response advertises them via ``Accept-CH`` (see
# ``client_hints_middleware`` in Server.py). They let us recover the real
# device model and Android version that the reduced UA string hides.
_CLIENT_HINT_HEADERS = {
    "platform": "Sec-CH-UA-Platform",
    "platform_version": "Sec-CH-UA-Platform-Version",
    "model": "Sec-CH-UA-Model",
    "mobile": "Sec-CH-UA-Mobile",
}


def get_client_hints(request) -> dict:
    hints = {}
    for key, header in _CLIENT_HINT_HEADERS.items():
        value = request.headers.get(header)
        if value:
            hints[key] = value
    return hints


class AuthRoutes:
    def __init__(self, server):
        self.server = server

    @staticmethod
    def _verify_telegram_login_widget(data: dict) -> bool:
        """Verify Telegram Login Widget payload using bot token.

        https://core.telegram.org/widgets/login#checking-authorization
        """
        try:
            received_hash = str(data.get("hash", ""))
            if not received_hash or not config.bot_token:
                return False
            auth_date = int(data.get("auth_date", 0))
        except Exception:
            return False

        # Reject stale auth data (24h window).
        if abs(int(time.time()) - auth_date) > 86400:
            return False

        check_pairs = []
        for key, value in data.items():
            if key == "hash":
                continue
            if value is None:
                continue
            check_pairs.append(f"{key}={value}")
        check_pairs.sort()
        data_check_string = "\n".join(check_pairs)
        secret_key = hashlib.sha256(config.bot_token.encode("utf-8")).digest()
        calculated_hash = hmac.new(
            secret_key,
            data_check_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(calculated_hash, received_hash)

    @staticmethod
    def _verify_telegram_webapp_init_data(init_data: str):
        """Verify Telegram Mini App initData and return user dict or None.

        https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
        """
        if not init_data or not config.bot_token:
            return None
        try:
            from urllib.parse import parse_qsl
            import json as _json
            parsed = dict(parse_qsl(init_data, keep_blank_values=True))
            received_hash = parsed.get("hash")
            if not received_hash:
                return None
            auth_date = int(parsed.get("auth_date", 0))
        except Exception:
            return None

        if abs(int(time.time()) - auth_date) > 86400:
            return None

        check_pairs = []
        for key, value in parsed.items():
            if key == "hash":
                continue
            check_pairs.append(f"{key}={value}")
        check_pairs.sort()
        data_check_string = "\n".join(check_pairs)
        secret_key = hmac.new(b"WebAppData", config.bot_token.encode("utf-8"), hashlib.sha256).digest()
        calculated_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calculated_hash, str(received_hash)):
            return None

        try:
            import json as _json
            user = _json.loads(parsed.get("user", "{}"))
            if not isinstance(user, dict):
                return None
            return user
        except Exception:
            return None

    async def login_page(self, request):
        session = auth.get_web_session(request.cookies.get(auth.SESSION_COOKIE))
        if not config.web_auth_enabled or session:
            raise web.HTTPFound('/')

        blocked, blocked_until = auth.is_blocked(request)
        if blocked:
            remaining = int(blocked_until - time.time())
            context = {
                "title_key": "web.login.title",
                "error": lang.get("web.login.blocked_minutes", "Blocked. Try again in {minutes} min").replace("{minutes}", str(max(1, -(-remaining // 60)))),
                "blocked": True,
                "require_2fa": False,
                "req_id": "",
            }
            resp = aiohttp_jinja2.render_template("login.html", request, context)
            if request.cookies.get("telesender_2fa_req"):
                resp.del_cookie("telesender_2fa_req", path="/")
            return resp

        cookie_req_id = request.cookies.get("telesender_2fa_req", "").strip()
        require_2fa = request.query.get("require_2fa") == "1" or bool(cookie_req_id)
        req_id = request.query.get("req_id", "").strip() or cookie_req_id
        error = None
        if request.query.get("error") == "1":
            error = lang.get("web.login.error", "Invalid password")
        elif request.query.get("error") == "2fa_failed":
            error = lang.get("web.login.two_factor_send_failed", "Failed to send 2FA request to Telegram. Ensure inline bot is active.")
        elif request.query.get("error") == "telegram_not_allowed":
            uid = request.query.get("uid", "")
            error = lang.get("web.login.not_allowed", "You are not in the allowed users list. Your ID: {user_id}. Add it to ALLOWED_USERS in .env").replace("{user_id}", uid)
        elif request.query.get("error") == "telegram_invalid":
            error = lang.get("web.login.telegram_error", "Telegram login error")
        elif request.query.get("error") == "bot_link_invalid":
            error = lang.get("web.login.bot_link_invalid", "This login link is invalid or has expired. Please request a new one from the bot.")

        two_factor = self.server.two_factor_manager
        if req_id:
            active_req = two_factor.get_request(req_id)
            if not active_req or active_req.status != "pending":
                require_2fa = False
                req_id = ""

        context = {
            "title_key": "web.login.title",
            "error": error,
            "blocked": False,
            "require_2fa": require_2fa,
            "req_id": req_id,
        }
        resp = aiohttp_jinja2.render_template("login.html", request, context)
        if cookie_req_id and not require_2fa:
            resp.del_cookie("telesender_2fa_req", path="/")
        return resp

    async def bot_login(self, request):
        """Exchange a one-time bot-issued login code for a web session.

        The code is generated only after the bot verifies the Telegram user ID
        from callback_query.from.id against the existing allowed users list.
        """
        if not config.web_auth_enabled:
            raise web.HTTPFound('/')

        code = request.query.get("code", "").strip()
        telegram_id = consume_bot_login_code(code)
        if telegram_id is None:
            raise web.HTTPFound('/login?error=bot_link_invalid')

        if telegram_id not in config.allowed_users:
            raise web.HTTPFound(f'/login?error=telegram_not_allowed&uid={telegram_id}')

        auth.clear_failed_attempts(request)
        session_id = auth.create_web_session(method="bot", telegram_id=telegram_id)
        resp = web.HTTPFound('/')
        auth.set_session_cookie(resp, session_id)
        resp.del_cookie('telesender_2fa_req', path='/')
        log.info(f"Bot button login: user {telegram_id} logged in")
        return resp

    async def login_post(self, request):
        is_json = (
            request.content_type == "application/json"
            or request.headers.get("X-Requested-With") == "XMLHttpRequest"
        )

        blocked, blocked_until = auth.is_blocked(request)
        if blocked:
            if is_json:
                remaining = int(blocked_until - time.time())
                return web.json_response(
                    {"error": lang.get("web.login.blocked_minutes", "Blocked. Try again in {minutes} min").replace("{minutes}", str(max(1, -(-remaining // 60))))},
                    status=429,
                )
            return web.HTTPFound('/login')

        password = ""
        try:
            if request.content_type == "application/json":
                json_data = await request.json()
                password = str(json_data.get("password", "")).strip()
            else:
                data = await request.post()
                password = str(data.get("password", "")).strip()
        except Exception:
            password = ""

        if config.web_auth_password and password == config.web_auth_password:
            auth.clear_failed_attempts(request)

            if config.web_auth_2fa:
                two_factor = self.server.two_factor_manager
                client_ip = auth.get_client_ip(request)
                user_agent = (request.headers.get("User-Agent") or "Unknown")[:512]
                req = two_factor.create_request(
                    ip=client_ip,
                    user_agent=user_agent,
                    hints=get_client_hints(request),
                )

                sent = False
                bot = getattr(self.server, "bot", None)
                if bot and getattr(bot, "inline", None):
                    sent = await bot.inline.send_2fa_request(req)

                if not sent:
                    log.error("Failed to send 2FA request via inline bot")
                    if is_json:
                        return web.json_response(
                            {"error": lang.get("web.login.two_factor_send_failed", "Failed to send 2FA request to Telegram.")},
                            status=500,
                        )
                    return web.HTTPFound('/login?error=2fa_failed')

                if is_json:
                    resp = web.json_response({
                        "require_2fa": True,
                        "request_id": req.request_id,
                    })
                else:
                    resp = web.HTTPFound(f'/login?require_2fa=1&req_id={req.request_id}')

                resp.set_cookie(
                    'telesender_2fa_req',
                    req.request_id,
                    httponly=True,
                    max_age=120,
                    samesite='Lax',
                    path='/',
                )
                return resp

            session_id = auth.create_web_session(method="password")
            if is_json:
                resp = web.json_response({"success": True, "redirect": "/"})
            else:
                resp = web.HTTPFound('/')

            auth.set_session_cookie(resp, session_id)
            resp.del_cookie('telesender_2fa_req', path='/')
            return resp
        else:
            is_blocked_now = auth.record_failed_attempt(request)
            if is_json:
                if is_blocked_now:
                    return web.json_response(
                        {"error": lang.get("web.login.blocked", "Blocked. Try again later.")},
                        status=429,
                    )
                return web.json_response(
                    {"error": lang.get("web.login.error", "Invalid password")},
                    status=401,
                )
            if is_blocked_now:
                return web.HTTPFound('/login')
            return web.HTTPFound('/login?error=1')

    async def check_2fa_status(self, request):
        """Poll the status of an ongoing 2FA request."""
        req_id = request.query.get("req_id", "").strip()
        if not req_id:
            req_id = request.cookies.get("telesender_2fa_req", "").strip()
        if not req_id:
            return web.json_response({"error": "missing req_id"}, status=400)

        two_factor = self.server.two_factor_manager
        req = two_factor.get_request(req_id)
        if not req:
            resp = web.json_response({"status": "expired"})
            resp.del_cookie('telesender_2fa_req', path='/')
            return resp

        status = req.status
        if status == "approved":
            session_id = auth.create_web_session(method="password_2fa")
            resp = web.json_response({"status": "approved"})
            auth.set_session_cookie(resp, session_id)
            resp.del_cookie('telesender_2fa_req', path='/')
            return resp

        if status in ("rejected", "expired", "cancelled"):
            resp = web.json_response({"status": status})
            resp.del_cookie('telesender_2fa_req', path='/')
            return resp

        return web.json_response({"status": status})

    async def cancel_2fa(self, request):
        """Cancel pending 2FA authentication request and clean up state."""
        req_id = ""
        try:
            if request.content_type == "application/json":
                data = await request.json()
                req_id = str(data.get("req_id", "")).strip()
            else:
                data = await request.post()
                req_id = str(data.get("req_id", "")).strip()
        except Exception:
            pass

        if not req_id:
            req_id = request.query.get("req_id", "").strip()
        if not req_id:
            req_id = request.cookies.get("telesender_2fa_req", "").strip()

        if req_id:
            bot = getattr(self.server, "bot", None)
            if bot and getattr(bot, "inline", None):
                await bot.inline.cancel_2fa_request(req_id)
            else:
                two_factor = self.server.two_factor_manager
                two_factor.resolve_request(req_id, "cancelled")

        is_json = (
            request.content_type == "application/json"
            or request.headers.get("X-Requested-With") == "XMLHttpRequest"
        )
        if is_json:
            resp = web.json_response({"success": True})
        else:
            resp = web.HTTPFound('/login')

        resp.del_cookie('telesender_2fa_req', path='/')
        return resp

    async def logout(self, request):
        session_id = request.cookies.get(auth.SESSION_COOKIE)
        auth.delete_web_session(session_id)
        resp = web.HTTPFound('/login')
        auth.clear_session_cookie(resp)
        resp.del_cookie('telesender_2fa_req', path='/')
        return resp

    async def telegram_login(self, request):
        """Handle Telegram Login Widget callback.

        Backend verifies Telegram signature/auth_date, then checks Telegram ID
        against the existing allowed users list.
        """
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": lang.get("web.login.telegram_error", "Telegram login error")}, status=400)

        user = data.get("user", {}) if isinstance(data.get("user"), dict) else data
        if not isinstance(user, dict):
            return web.json_response({"error": lang.get("web.login.telegram_error", "Telegram login error")}, status=400)

        try:
            user_id = int(user.get("id"))
        except Exception:
            return web.json_response({"error": lang.get("web.login.telegram_error", "Telegram login error")}, status=400)

        if not self._verify_telegram_login_widget(user):
            log.warning(f"Telegram Login Widget: invalid signature/auth data for user {user_id}")
            return web.json_response({"error": lang.get("web.login.telegram_error", "Telegram login error")}, status=403)

        first_name = str(user.get("first_name", ""))
        if user_id in config.allowed_users:
            auth.clear_failed_attempts(request)
            session_id = auth.create_web_session(method="telegram_widget", telegram_id=user_id)
            resp = web.json_response({"redirect": "/"})
            auth.set_session_cookie(resp, session_id)
            resp.del_cookie('telesender_2fa_req', path='/')
            log.info(f"Telegram Login Widget: user {user_id} ({first_name}) logged in")
            return resp
        else:
            log.warning(f"Telegram Login Widget: user {user_id} ({first_name}) not in allowed_users")
            return web.json_response({
                "error": lang.get("web.login.not_allowed", "You are not in the allowed users list. Your ID: {user_id}. Add it to ALLOWED_USERS in .env").replace("{user_id}", str(user_id))
            }, status=403)

    async def telegram_webapp_login(self, request):
        """Handle Telegram Mini App (Web App) initData for automatic login.

        Allows the "Open web interface" bot button to open the web app directly
        (single tap) while backend verifies the opener's Telegram ID via signed
        initData and checks it against the existing allowed users list.
        """
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": lang.get("web.login.telegram_error", "Telegram login error")}, status=400)

        init_data = str(data.get("initData") or data.get("init_data") or "")
        user = self._verify_telegram_webapp_init_data(init_data)
        if not user:
            return web.json_response({"error": lang.get("web.login.telegram_error", "Telegram login error")}, status=403)

        try:
            user_id = int(user.get("id"))
        except Exception:
            return web.json_response({"error": lang.get("web.login.telegram_error", "Telegram login error")}, status=400)

        if user_id in config.allowed_users:
            auth.clear_failed_attempts(request)
            session_id = auth.create_web_session(method="telegram_webapp", telegram_id=user_id)
            resp = web.json_response({"redirect": "/"})
            auth.set_session_cookie(resp, session_id)
            resp.del_cookie('telesender_2fa_req', path='/')
            log.info(f"Telegram WebApp: user {user_id} logged in")
            return resp
        else:
            log.warning(f"Telegram WebApp: user {user_id} not in allowed_users")
            return web.json_response({
                "error": lang.get("web.login.not_allowed", "You are not in the allowed users list. Your ID: {user_id}. Add it to ALLOWED_USERS in .env").replace("{user_id}", str(user_id))
            }, status=403)

    def register(self, app: web.Application):
        app.router.add_get('/login', self.login_page)
        app.router.add_get('/auth/bot-login', self.bot_login)
        app.router.add_post('/auth/telegram-login', self.telegram_login)
        app.router.add_post('/auth/telegram-webapp', self.telegram_webapp_login)
        app.router.add_post('/auth/login', self.login_post)
        app.router.add_get('/auth/2fa/status', self.check_2fa_status)
        app.router.add_post('/auth/2fa/cancel', self.cancel_2fa)
        app.router.add_get('/auth/2fa/cancel', self.cancel_2fa)
        app.router.add_post('/auth/logout', self.logout)
