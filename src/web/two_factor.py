#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Two-Factor Authentication (2FA) manager for TeleSender Web Interface."""

import asyncio
import hashlib
import hmac
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Dict, Optional

from src.config.config import config


def save_2fa_setting(enabled: bool) -> None:
    """Applies the 2FA setting to the running config and persists it to .env."""
    config.web_auth_2fa = enabled
    os.environ["WEB_AUTH_2FA"] = "true" if enabled else "false"

    env_path = Path(".env")
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    value = "true" if enabled else "false"

    for index, line in enumerate(lines):
        if line.strip().startswith("WEB_AUTH_2FA="):
            lines[index] = f"WEB_AUTH_2FA={value}"
            break
    else:
        lines.append(f"WEB_AUTH_2FA={value}")

    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class TwoFactorRequest:
    """Represents a pending 2FA login verification request."""

    def __init__(
        self,
        request_id: str,
        ip: str,
        user_agent: str,
        client_secret_hash: str,
        hints: Optional[dict] = None,
        timeout: float = 120.0,
        purpose: str = "login",
    ):
        self.request_id = request_id
        self.ip = ip
        self.user_agent = user_agent
        self.client_secret_hash = client_secret_hash
        self.hints = hints or {}
        self.purpose = purpose  # "login" or "disable_2fa"
        self.created_at = time.time()
        self.expires_at = self.created_at + timeout
        self.status = "pending"  # "pending", "approved", "rejected", "expired", "consumed"
        self.chat_id: Optional[int] = None
        self.message_id: Optional[int] = None
        # Transient plain secret returned only to creator; not persisted/logged.
        self.client_secret: Optional[str] = None

    @property
    def is_expired(self) -> bool:
        return time.time() > self.expires_at

    def verify_client_secret(self, secret: str) -> bool:
        if not secret or not self.client_secret_hash:
            return False
        expected = self.client_secret_hash.encode("utf-8")
        got = hashlib.sha256(str(secret).encode("utf-8")).hexdigest().encode("utf-8")
        return hmac.compare_digest(expected, got)


class TwoFactorManager:
    """Manages active 2FA verification requests and their lifecycle."""

    def __init__(self):
        self._requests: Dict[str, TwoFactorRequest] = {}
        self._lock = threading.Lock()

    def create_request(
        self,
        ip: str,
        user_agent: str,
        hints: Optional[dict] = None,
        timeout: float = 120.0,
        purpose: str = "login",
    ) -> TwoFactorRequest:
        with self._lock:
            self.cleanup()
            request_id = secrets.token_hex(16)
            client_secret = secrets.token_urlsafe(32)
            client_secret_hash = hashlib.sha256(client_secret.encode("utf-8")).hexdigest()
            req = TwoFactorRequest(
                request_id=request_id,
                ip=ip,
                user_agent=user_agent,
                client_secret_hash=client_secret_hash,
                hints=hints,
                timeout=timeout,
                purpose=purpose,
            )
            req.client_secret = client_secret
            self._requests[request_id] = req
            return req

    def get_request(self, request_id: str) -> Optional[TwoFactorRequest]:
        with self._lock:
            self.cleanup()
            req = self._requests.get(request_id)
            if req and req.is_expired and req.status == "pending":
                req.status = "expired"
            return req

    def resolve_request(self, request_id: str, status: str) -> bool:
        with self._lock:
            req = self._requests.get(request_id)
            if not req:
                return False
            if req.is_expired and req.status == "pending":
                req.status = "expired"
            if req.status != "pending":
                return False
            req.status = status
            return True

    def consume_approved_request(
        self,
        request_id: str,
        purpose: str = "login",
        client_secret: Optional[str] = None,
    ) -> Optional[TwoFactorRequest]:
        """Atomically consume an approved request once."""
        with self._lock:
            self.cleanup()
            req = self._requests.get(request_id)
            if not req:
                return None
            if req.is_expired and req.status == "pending":
                req.status = "expired"
            if req.status != "approved" or req.purpose != purpose:
                return None
            if not req.verify_client_secret(client_secret or ""):
                return None
            req.status = "consumed"
            return req

    def cleanup(self) -> None:
        now = time.time()
        expired_ids = [
            req_id
            for req_id, req in self._requests.items()
            if now > (req.expires_at + 300)
        ]
        for req_id in expired_ids:
            self._requests.pop(req_id, None)


# Singleton instance
two_factor_manager = TwoFactorManager()
