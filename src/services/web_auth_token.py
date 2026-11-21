#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deprecated web auto-login token helpers.

The old token mechanism derived from bot token and web password has been
removed. Bot-initiated web login now uses one-time codes generated after
verifying Telegram callback_query.from.id against the existing allowed users
list (see src.web.sessions). This module is kept only to avoid import errors
from stale bytecode in external environments.
"""
