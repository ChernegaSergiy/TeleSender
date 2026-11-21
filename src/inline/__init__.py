#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TeleSender Inline System.

Provides inline buttons, forms, and interactive UI elements for plugins
through a secondary Telegram bot managed via Telethon.
"""

from .form import FormManager
from .manager import InlineManager
from .token_obtainer import TokenObtainer
from .types import ButtonDefinition, InlineCall, InlineForm

__all__ = [
    "InlineManager",
    "FormManager",
    "TokenObtainer",
    "InlineCall",
    "InlineForm",
    "ButtonDefinition",
]
