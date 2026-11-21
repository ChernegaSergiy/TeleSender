#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Web routes package exports."""

from src.web.routes.pages import PageRoutes
from src.web.routes.api_tasks import TaskApiRoutes
from src.web.routes.api_plugins import PluginApiRoutes
from src.web.routes.api_settings import SettingsApiRoutes
from src.web.routes.auth import AuthRoutes

__all__ = ["PageRoutes", "TaskApiRoutes", "PluginApiRoutes", "SettingsApiRoutes", "AuthRoutes"]
