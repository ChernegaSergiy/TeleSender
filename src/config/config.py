#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Config.py - Configuration from .env
"""

import os
import socket
from dotenv import load_dotenv

# Load .env
load_dotenv()


class Config:
    """Bot configuration"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._load()
        return cls._instance

    def _load(self):
        # Telegram
        self.api_id = int(os.getenv('API_ID', 0))
        self.api_hash = os.getenv('API_HASH', '')
        self.phone_number = os.getenv('PHONE_NUMBER', '')
        self.my_user_id = int(os.getenv('MY_USER_ID', 0))
        self.bot_token = os.getenv('BOT_TOKEN', os.getenv('INLINE_BOT_TOKEN', '')).strip()
        self.bot_username = os.getenv('BOT_USERNAME', '').strip().lstrip('@')

        # Allowed users
        allowed = os.getenv('ALLOWED_USERS', '')
        self.allowed_users = [
            int(x.strip()) for x in allowed.split(',') if x.strip().isdigit()
        ]

        # Language
        self.language = os.getenv('LANGUAGE', 'en')

        # Web
        self.web_enabled = os.getenv('WEB_ENABLED', 'true').lower() == 'true'
        self.web_host = os.getenv('WEB_HOST', '0.0.0.0')
        self.web_port = int(os.getenv('WEB_PORT', 8000))
        # Public URL used by Telegram buttons (for example a tunnel or domain).
        # Empty means "build it from WEB_HOST/WEB_PORT".
        self.web_public_url = os.getenv('WEB_PUBLIC_URL', '').strip()
        # Automatic HTTPS tunnel. When enabled, the tunnel service replaces
        # web_public_url with the HTTPS URL assigned by the provider.
        # Provider: "auto" or "cloudflared" (ssh is not implemented).
        self.tunnel_enabled = os.getenv('TUNNEL_ENABLED', 'false').lower() == 'true'
        self.tunnel_provider = os.getenv('TUNNEL_PROVIDER', 'auto').strip().lower()
        self.tunnel_remote_port = int(os.getenv('TUNNEL_REMOTE_PORT', '80') or 80)
        self.tunnel_start_timeout = float(os.getenv('TUNNEL_START_TIMEOUT', '20') or 20)
        # Keep the tunnel process alive when the bot exits and reuse its URL
        # on the next start. A new quick-tunnel hostname would invalidate every
        # browser session cookie, so this is on by default.
        self.tunnel_keep_alive = os.getenv('TUNNEL_KEEP_ALIVE', 'true').lower() == 'true'
        self.web_auth_enabled = os.getenv('WEB_AUTH_ENABLED', 'false').lower() == 'true'
        self.web_auth_password = os.getenv('WEB_AUTH_PASSWORD', '')
        self.web_auth_2fa = os.getenv('WEB_AUTH_2FA', 'false').lower() == 'true'
        # Let the Telegram Mini App button log in without the password: the
        # link carries a short-lived signed token, the web server exchanges
        # it for the regular session cookie.
        self.web_autologin_enabled = os.getenv('WEB_AUTOLOGIN_ENABLED', 'true').lower() == 'true'
        self.web_autologin_ttl = int(os.getenv('WEB_AUTOLOGIN_TTL_HOURS', '24') or 24) * 3600
        self.web_auth_max_attempts = int(os.getenv('WEB_AUTH_MAX_ATTEMPTS', '5'))
        self.web_auth_lockout_minutes = int(os.getenv('WEB_AUTH_LOCKOUT_MINUTES', '15'))
        # Maximum size of a file uploaded from the web interface (MB).
        self.web_max_upload_mb = int(os.getenv('WEB_MAX_UPLOAD_MB', '200') or 200)
        # Rendered HTML page cache. Set to 0 to disable cached pages.
        self.web_page_cache_ttl = float(os.getenv('WEB_PAGE_CACHE_TTL', '15') or 0)
        self.web_page_cache_size = int(os.getenv('WEB_PAGE_CACHE_SIZE', '128') or 0)
        self.web_page_cache_bytes = int(os.getenv('WEB_PAGE_CACHE_BYTES', str(16 * 1024 * 1024)) or 0)

        # Database
        self.db_path = os.getenv('DB_PATH', 'data/tasks.db')

        # Logging
        self.log_file = os.getenv('LOG_FILE', 'data/telesender.log')
        self.log_level = os.getenv('LOG_LEVEL', 'DEBUG').upper()
        self.log_console = os.getenv('LOG_CONSOLE', 'false').lower() == 'true'
        self.log_max_bytes = int(os.getenv('LOG_MAX_BYTES', str(10 * 1024 * 1024)))
        self.log_backup_count = int(os.getenv('LOG_BACKUP_COUNT', '5'))

        # Session
        self.session_path = os.getenv('SESSION_PATH', 'data/session')

        # Directories
        self.plugins_dir = os.getenv('PLUGINS_DIR', 'plugins')
        self.languages_dir = os.getenv('LANGUAGES_DIR', 'languages')

    @staticmethod
    def _detect_local_ip() -> str:
        """Best-effort detection of the machine's LAN/public IP address.

        Connecting a UDP socket does not send any packets, but it makes the
        OS pick the address of the interface used for outgoing traffic, which
        is the address other devices can reach. Falls back to the hostname
        lookup and finally to the loopback address.
        """
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                probe.connect(("8.8.8.8", 80))
                address = probe.getsockname()[0]
            if address and not address.startswith("0."):
                return address
        except OSError:
            pass
        try:
            address = socket.gethostbyname(socket.gethostname())
            if address and not address.startswith("127."):
                return address
        except OSError:
            pass
        return "127.0.0.1"

    @property
    def web_url(self) -> str:
        """URL of the web interface, usable in Telegram URL buttons.

        ``WEB_PUBLIC_URL`` wins when set. Otherwise the URL is built from
        ``WEB_HOST``/``WEB_PORT``. A wildcard bind address is replaced with
        the detected machine IP, because ``localhost`` would point at the
        Telegram client's own device instead of the bot.
        """
        public = (self.web_public_url or "").strip()
        if public:
            return public.rstrip("/")
        host = (self.web_host or "").strip()
        if host in ("", "0.0.0.0", "::", "[::]"):
            host = self._detect_local_ip()
        return f"http://{host}:{self.web_port}"

    def get(self, key: str, default=None):
        """Get config value by key"""
        return getattr(self, key, default)


# Singleton instance
config = Config()
