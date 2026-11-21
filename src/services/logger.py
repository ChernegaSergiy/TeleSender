#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Logging service with quiet rotating file output and optional console output."""

import logging
import os
from datetime import datetime
from logging.handlers import RotatingFileHandler

from src.config.config import config


class ConsoleColors:
    RESET = "\033[0m"
    TIME = "\033[90m"
    DEBUG = "\033[36m"
    INFO = "\033[92m"
    WARNING = "\033[93m"
    ERROR = "\033[91m"
    CRITICAL = "\033[95m"

    PREFIX_DEBUG = "DEBUG"
    PREFIX_INFO = "INFO"
    PREFIX_WARNING = "WARNING"
    PREFIX_ERROR = "ERROR"
    PREFIX_CRITICAL = "CRITICAL"


class ColoredFormatter(logging.Formatter):
    """Custom formatter for colorized console output."""

    def format(self, record):
        level = record.levelno
        if level == logging.DEBUG:
            prefix = f"{ConsoleColors.PREFIX_DEBUG}{ConsoleColors.RESET}"
            color = ConsoleColors.DEBUG
        elif level == logging.INFO:
            prefix = f"{ConsoleColors.PREFIX_INFO}{ConsoleColors.RESET}"
            color = ConsoleColors.INFO
        elif level == logging.WARNING:
            prefix = f"{ConsoleColors.PREFIX_WARNING}{ConsoleColors.RESET}"
            color = ConsoleColors.WARNING
        elif level == logging.ERROR:
            prefix = f"{ConsoleColors.PREFIX_ERROR}{ConsoleColors.RESET}"
            color = ConsoleColors.ERROR
        elif level == logging.CRITICAL:
            prefix = f"{ConsoleColors.PREFIX_CRITICAL}{ConsoleColors.RESET}"
            color = ConsoleColors.CRITICAL
        else:
            prefix = record.levelname
            color = ConsoleColors.RESET

        time_str = f"{ConsoleColors.TIME}{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}{ConsoleColors.RESET}"
        msg = f"{color}{record.msg}{ConsoleColors.RESET}"
        return f"{time_str} | {prefix} | {msg}"


class Logger:
    """Application logger singleton."""

    _instance = None
    NOISY_LOGGERS = (
        "aiohttp",
        "aiohttp.access",
        "asyncio",
        "charset_normalizer",
        "httpx",
        "PIL",
        "telethon",
        "urllib3",
        "websockets",
    )

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._setup()
        return cls._instance

    @staticmethod
    def _resolve_level(value) -> int:
        if isinstance(value, int):
            return value
        return getattr(logging, str(value).upper(), logging.DEBUG)

    def _setup(self):
        log_file = config.log_file
        log_dir = os.path.dirname(log_file)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)

        level = self._resolve_level(getattr(config, "log_level", "DEBUG"))
        root_logger = logging.getLogger()
        root_logger.setLevel(level)

        for handler in list(root_logger.handlers):
            if getattr(handler, "_telesender_handler", False):
                root_logger.removeHandler(handler)

        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=getattr(config, "log_max_bytes", 10 * 1024 * 1024),
            backupCount=getattr(config, "log_backup_count", 5),
            encoding="utf-8",
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
        )
        file_handler._telesender_handler = True
        root_logger.addHandler(file_handler)

        if getattr(config, "log_console", False):
            console_handler = logging.StreamHandler()
            console_handler.setLevel(logging.INFO)
            console_handler.setFormatter(ColoredFormatter())
            console_handler._telesender_handler = True
            root_logger.addHandler(console_handler)

        for name in self.NOISY_LOGGERS:
            logging.getLogger(name).setLevel(logging.WARNING)

        self.logger = logging.getLogger("TeleSender")
        self.logger.handlers.clear()
        self.logger.setLevel(logging.NOTSET)
        self.logger.propagate = True

    def debug(self, msg: str):
        self.logger.debug(msg)

    def info(self, msg: str):
        self.logger.info(msg)

    def warning(self, msg: str):
        self.logger.warning(msg)

    def error(self, msg: str):
        self.logger.error(msg)

    def exception(self, msg: str):
        self.logger.exception(msg)

    def critical(self, msg: str):
        self.logger.critical(msg)


log = Logger()
