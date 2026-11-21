#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared generator for developer-ready plugin scaffolds.

Both the console ("13. Create plugin") and the web API use this module so a
new plugin always gets the same structure, documentation, translations and
API examples.
"""

import json
import os
import re
import shutil
from typing import Dict, Optional

from core.plugin import generate_plugin_id
from src.services.plugin_translations import PLUGIN_TRANSLATIONS


PLUGIN_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_COMMAND_RE = re.compile(r"[^a-z0-9_]+")


def validate_plugin_name(name: str) -> str:
    """Validates a plugin folder name and returns it unchanged."""
    name = str(name or "").strip()
    if not name:
        raise ValueError("Plugin name is required")
    if not PLUGIN_NAME_RE.fullmatch(name):
        raise ValueError(
            "Invalid plugin name. Use only letters, digits, _ and -"
        )
    return name


def plugin_class_name(name: str) -> str:
    """Builds a valid Python class name from a plugin folder name."""
    parts = [part for part in re.split(r"[^A-Za-z0-9]+", name) if part]
    class_name = "".join(part[:1].upper() + part[1:] for part in parts) or "Plugin"
    if not re.match(r"^[A-Za-z_]", class_name):
        class_name = f"Plugin{class_name}"
    return class_name


def plugin_command_name(name: str) -> str:
    """Builds a safe Telegram command name from a plugin folder name."""
    command = _COMMAND_RE.sub("_", name.lower()).strip("_")
    if not command:
        command = "demo"
    if command[0].isdigit():
        command = f"plugin_{command}"
    return command


def _render(template: str, **values) -> str:
    """Replaces __TOKEN__ placeholders without touching Python/Jinja braces."""
    for key, value in values.items():
        template = template.replace(f"__{key}__", str(value))
    return template


def build_manifest(name: str, author: str, description: str, category: str) -> Dict:
    """Builds the manifest dictionary written to manifest.json."""
    return {
        "name": name,
        "author": author,
        "version": "1.0.0",
        "description": description,
        "category": category,
        "id": generate_plugin_id(name, author),
        "requirements": [],
        "icon": "puzzle",
    }


def build_config(version: str = "1.0.0") -> Dict:
    """Default configuration written to config.json."""
    return {
        "_version": version,
        "enabled": True,
        "prefix": "",
        "greeting": "Hello",
        "demo_limit": 10,
    }


def build_plugin_code(name: str, class_name: str, command_name: str,
                      create_web_page: bool) -> str:
    """Builds a working plugin.py that demonstrates the real Plugin API."""
    web_import = (
        "from core import Plugin, command, inline, loop, page, watcher"
        if create_web_page
        else "from core import Plugin, command, inline, loop, watcher"
    )

    web_section = """

    # ------------------------------------------------------------------
    # Web page
    # ------------------------------------------------------------------
    @page(
        path="overview",
        template="index.html",
        title_key="web_page_title",
        icon="puzzle",       # Lucide name, emoji, URL, or a file in static/
        cache_ttl=0,         # 0 disables the HTML page cache for this page
    )
    async def overview_page(self, request):
        \"\"\"Page handler signature: (self, request).

        It may be sync or async. Return a dict to extend the template context,
        a web.StreamResponse to return a custom response, or None.
        \"\"\"
        description_keys = {
            "__COMMAND_NAME__": "cmd_demo_description",
            "__COMMAND_NAME___ping": "cmd_ping_description",
            "__COMMAND_NAME___config": "cmd_config_description",
            "__COMMAND_NAME___set": "cmd_set_description",
            "__COMMAND_NAME___menu": "cmd_menu_description",
        }

        commands = []
        for member in vars(type(self)).values():
            if not getattr(member, "_is_command", False):
                continue
            name = getattr(member, "_command_name", "")
            description = getattr(member, "_command_description", "") or ""
            translation_key = description_keys.get(name)
            if translation_key:
                translated = self.t(translation_key)
                if translated != translation_key:
                    description = translated
            commands.append({
                "name": name,
                "description": description,
                "usage": getattr(member, "_command_usage", "") or f".{name}",
                "aliases": list(getattr(member, "_command_aliases", []) or []),
            })
        commands.sort(key=lambda item: item["name"])

        watcher_count = sum(
            1 for member in vars(type(self)).values()
            if getattr(member, "_is_watcher", False)
        )
        loop_count = sum(
            1 for member in vars(type(self)).values()
            if getattr(member, "_is_loop", False)
        )
        inline_count = sum(
            1 for member in vars(type(self)).values()
            if getattr(member, "_is_inline", False)
        )

        static_version = "1"
        if self.path:
            static_dir = os.path.join(self.path, "static")
            try:
                static_version = str(int(max(
                    os.path.getmtime(os.path.join(static_dir, name))
                    for name in os.listdir(static_dir)
                )))
            except (OSError, ValueError):
                pass

        return {
            "plugin_version": self.version,
            "plugin_author": self.author,
            "plugin_category": self.category,
            "plugin_description": self.description,
            "plugin_folder": self.folder_name,
            "plugin_api_url": f"/plugin/{self.folder_name}/api/config",
            "plugin_reload_url": f"/api/plugins/{self.folder_name}/reload",
            "prefix": self.get_config("prefix", ""),
            "greeting": self.get_config("greeting", "Hello"),
            "demo_limit": self.get_config("demo_limit", 10),
            "enabled": bool(self.get_config("enabled", True)),
            "commands": commands,
            "command_count": len(commands),
            "watcher_count": watcher_count,
            "loop_count": loop_count,
            "inline_count": inline_count,
            "request_path": request.path,
            "static_url": f"/plugin/{self.folder_name}/static",
            "static_version": static_version,
        }

    async def on_page_render(self, page_path: str, request):
        \"\"\"Optional hook: extra values for every page of this plugin.

        It may be sync or async. Return a dict or None.
        \"\"\"
        if page_path != "overview":
            return {}
        return {"rendered_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

    def get_menu_items(self):
        \"\"\"Navigation metadata for this plugin's pages.

        This method MUST be synchronous. The page still has to be pinned in
        /plugins before it appears in the bottom navigation.
        \"\"\"
        return [{
            "name": self.t("menu_overview"),
            "url": "overview",   # relative -> /plugin/<folder>/overview
            "icon": "puzzle",
        }]
""" if create_web_page else ""

    return _render(
        '''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""__PLUGIN_NAME__ plugin.

Generated by TeleSender "Create plugin".

This file contains working examples of the Plugin API. Read README.md for the
full reference. Remove the examples you do not need.
"""

import asyncio
import os
import time
from datetime import datetime
from typing import Any, Dict, List

__WEB_IMPORT__
# @callback is registered but never dispatched by the current runtime.
# from core import callback


class __CLASS_NAME__Plugin(Plugin):
    """__PLUGIN_NAME__ plugin."""

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def on_load(self) -> None:
        """Called once, before main()."""
        self.logger.info(self.t("plugin_loaded", version=self.version))

    async def on_unload(self) -> None:
        """Called when the plugin is unloaded or reloaded."""
        self.logger.info(self.t("plugin_unloaded"))

    async def main(self) -> None:
        """Required by the Plugin base class. Runs after on_load()."""
        self.logger.info(self.t("plugin_main_started", name=self.name))

    async def on_config_change(self, key: str, old_value: Any, new_value: Any) -> None:
        """Defined by the base class, but NOT called automatically by the
        current runtime. Call it yourself after set_config() if you need it.
        """
        self.logger.info(self.t(
            "config_changed_log",
            field=key,
            old=repr(old_value),
            new=repr(new_value),
        ))

    # ------------------------------------------------------------------
    # Commands (prefix: ".")
    # ------------------------------------------------------------------
    @command(
        "__COMMAND_NAME__",
        aliases=["np"],     # demo alias; rename it if another plugin uses the same one
        description="__PLUGIN_NAME__ demo command",
        usage=".__COMMAND_NAME__",
    )
    async def demo_command(self, event):
        """Command handler signature: (self, event)."""
        prefix = self.get_config("prefix", "")
        greeting = self.get_config("greeting", "Hello")
        await event.reply(self.t(
            "demo_reply",
            prefix=prefix,
            greeting=greeting,
            name=self.name,
        ))

    @command(
        "__COMMAND_NAME___ping",
        description="Measure the Telegram round-trip latency",
        usage=".__COMMAND_NAME___ping",
    )
    async def ping_command(self, event):
        """Measure the Telegram round-trip and report the latency."""
        started = time.perf_counter()
        message = await event.reply(self.t("ping_measuring"))
        elapsed_ms = (time.perf_counter() - started) * 1000
        text = self.t("ping_result", ms=f"{elapsed_ms:.0f}")
        if message is not None and hasattr(message, "edit"):
            await message.edit(text)
        else:
            await event.reply(text)

    @command(
        "__COMMAND_NAME___config",
        description="Show the Telegram account and plugin configuration",
        usage=".__COMMAND_NAME___config",
    )
    async def config_command(self, event):
        """Read the Telegram account and configuration in one message."""
        me = await self.client.get_me()
        lines = [
            self.t("config_account", value=me.username or me.id),
            self.t("config_prefix", value=repr(self.get_config("prefix"))),
            self.t("config_greeting", value=repr(self.get_config("greeting"))),
            self.t("config_demo_limit", value=self.get_config("demo_limit")),
        ]
        await event.reply(chr(10).join(lines))

    @command(
        "__COMMAND_NAME___set",
        description="Change a plugin config value and save it",
        usage=".__COMMAND_NAME___set <key> <value>",
    )
    async def config_set_command(self, event):
        """Change prefix, greeting or demo_limit from Telegram."""
        allowed = ("prefix", "greeting", "demo_limit")
        parts = (event.raw_text or "").split(maxsplit=2)
        if len(parts) < 3:
            await event.reply(self.t(
                "config_set_usage",
                command=self.folder_name,
                keys=", ".join(allowed),
            ))
            return

        key = parts[1].lower()
        value = parts[2].strip()
        if key not in allowed:
            await event.reply(self.t(
                "config_set_invalid_key",
                field=key,
                keys=", ".join(allowed),
            ))
            return

        if key == "demo_limit":
            try:
                value = int(value)
            except ValueError:
                await event.reply(self.t("config_set_invalid_int", field=key))
                return

        old_value = self.get_config(key)
        if not self.set_config(key, value):
            await event.reply(self.t(
                "config_set_invalid_key",
                field=key,
                keys=", ".join(allowed),
            ))
            return
        await event.reply(self.t(
            "config_set_changed",
            field=key,
            old=old_value,
            new=value,
        ))

    # ------------------------------------------------------------------
    # Watchers (run only when no command handled the message)
    # ------------------------------------------------------------------
    @watcher(pattern="__COMMAND_NAME__", only_private=False)
    async def demo_watcher(self, event):
        """Runs for messages that contain the pattern."""
        # The bot and the owner share one Telegram account, so outgoing
        # messages must be handled too. Skip only outgoing replies: they are
        # generated by this bot (or by a command) and would cause a loop.
        if getattr(event, "out", False) and getattr(event, "is_reply", False):
            return
        text = (event.raw_text or "")[:80]
        await event.reply(self.t("watcher_demo_reply", name=self.name, text=text))

    @watcher(regex=True, pattern=r"^!demo", only_groups=True)
    async def regex_watcher(self, event):
        """Runs in groups for messages that match the regex."""
        if getattr(event, "out", False) and getattr(event, "is_reply", False):
            return
        text = (event.raw_text or "")[:80]
        await event.reply(self.t("watcher_regex_reply", name=self.name, text=text))

    # ------------------------------------------------------------------
    # Background loop
    # ------------------------------------------------------------------
    @loop(interval=3600.0, start_immediately=False)
    async def heartbeat(self):
        """Runs every hour. Keep it short; never block the event loop."""
        self.logger.debug(self.t(
            "heartbeat_log",
            time=datetime.now().strftime("%H:%M:%S"),
        ))

    # ------------------------------------------------------------------
    # Inline forms (interactive buttons via the inline bot)
    # ------------------------------------------------------------------
    def _menu_markup(self, like_count: int = 0):
        """Build the demo form buttons with the current like counter."""
        return [
            [
                {
                    "text": self.t("button_like", count=like_count),
                    "callback": self._on_like,
                    "args": [like_count],
                },
                {
                    "text": self.t("button_alert"),
                    "action": "answer",
                    "message": self.t("button_alert_text"),
                    "show_alert": True,
                },
            ],
            [
                {"text": self.t("button_time"), "callback": self._on_time},
                {"text": self.t("button_github"), "url": "https://github.com"},
            ],
            [{"text": self.t("button_close"), "action": "close"}],
        ]

    @command(
        "__COMMAND_NAME___menu",
        description="Open the inline demo menu",
        usage=".__COMMAND_NAME___menu",
    )
    async def inline_menu(self, event):
        """Show a safe demo form. Requires the inline bot to be ready."""
        if not self.inline or not self.inline.is_ready:
            await event.reply(self.t("inline_unavailable"))
            return

        await self.inline.form(
            text=self.t("form_title", name=self.name),
            message=event,
            reply_markup=self._menu_markup(0),
            force_me=True,   # only the command author can press the buttons
            ttl=300,         # the form expires after 5 minutes
        )

    async def _on_like(self, call, count: int = 0):
        """Button callback signature: (call, *args, **kwargs)."""
        new_count = count + 1
        await call.answer(self.t("like_answer", count=new_count))
        await call.edit(
            self.t("form_liked", count=new_count),
            reply_markup=self._menu_markup(new_count),
        )

    async def _on_time(self, call):
        await call.answer(self.t("time_answer"))
        await call.edit(
            self.t("form_time", time=datetime.now().strftime("%H:%M:%S"))
        )

    # ------------------------------------------------------------------
    # Inline-query handler
    # ------------------------------------------------------------------
    @inline("__COMMAND_NAME__")
    async def inline_query(self, event):
        """Called for every inline query that is not an active form.

        Return a list of results. Filter event.text yourself: the runtime
        calls every registered @inline handler for every query.
        """
        if not event.text.startswith("__COMMAND_NAME__ "):
            return []
        text = event.text[len("__COMMAND_NAME__ "):].strip() or "__PLUGIN_NAME__"
        return [event.builder.article(
            title="__PLUGIN_NAME__",
            text=f"<b>{text}</b>",
            description=self.t("inline_result_description"),
            parse_mode="html",
        )]
__WEB_SECTION__
    # ------------------------------------------------------------------
    # Optional: extra web API routes (POST, etc.)
    # ------------------------------------------------------------------
    # The generic plugin page route is GET-only. This hook is called once when
    # the web server starts for each loaded plugin. If you load this plugin
    # later, restart the bot/web server.
    def register_web_api_routes(self, app):
        app.router.add_post(
            f"/plugin/{self.folder_name}/api/config",
            self._web_save_config,
        )

    async def _web_save_config(self, request):
        from aiohttp import web
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": self.t("api_invalid_json")}, status=400)

        allowed = ("prefix", "greeting", "demo_limit")
        values = data.get("values")
        if not isinstance(values, dict):
            key = str(data.get("key") or "")
            values = {key: data.get("value")}

        cleaned = {}
        for key, value in values.items():
            if key not in allowed:
                return web.json_response(
                    {"error": self.t("api_unknown_key", field=key)}, status=400
                )
            if key == "demo_limit":
                try:
                    value = int(value)
                except (TypeError, ValueError):
                    return web.json_response(
                        {"error": self.t("api_invalid_int", field=key)}, status=400
                    )
            cleaned[key] = value

        # The route is registered once when the web server starts. After the
        # plugin is reloaded, this bound method still belongs to the old
        # instance, so resolve the instance that is loaded right now and save
        # there. Otherwise commands keep using stale in-memory values.
        target = self
        applied = True
        service = getattr(self.bot, "plugin_service", None) if self.bot else None
        if service is not None:
            current = service.get_plugin_instance(self.folder_name)
            if current is not None:
                target = current
            else:
                applied = False

        for key, value in cleaned.items():
            target.set_config(key, value)

        return web.json_response({
            "ok": True,
            "saved": sorted(cleaned),
            "applied": applied,
            "config": target._config_data,
        })
''',
        WEB_SECTION=web_section,
        PLUGIN_NAME=name,
        CLASS_NAME=class_name,
        COMMAND_NAME=command_name,
        WEB_IMPORT=web_import,
    )

def build_template(name: str) -> str:
    """Builds templates/index.html for the generated plugin."""
    return _render(
        '''{% extends "base.html" %}

{# base.html currently defines only the "content" block. Put CSS/JS links
   inside content so they are actually rendered. #}
{% block content %}
<link rel="stylesheet" href="{{ plugin_static_url }}/style.css?v={{ static_version }}">

<div class="page-header">
    <h1 class="page-title">{{ page_title }}</h1>
    <div class="page-header-actions">
        <span class="badge badge-info">{{ plugin_title }} v{{ plugin_version }}</span>
        {% if enabled %}
        <span class="badge badge-success">
            <span class="status-dot"></span>{{ plugin_t('web_page_enabled') }}
        </span>
        {% else %}
        <span class="badge badge-muted">
            <span class="status-dot offline"></span>{{ plugin_t('web_page_disabled') }}
        </span>
        {% endif %}
    </div>
</div>

<div class="grid grid-4">
    <div class="stat-card">
        <div class="stat-label">{{ plugin_t('web_page_stat_commands') }}</div>
        <div class="stat-value">{{ command_count }}</div>
    </div>
    <div class="stat-card">
        <div class="stat-label">{{ plugin_t('web_page_stat_watchers') }}</div>
        <div class="stat-value">{{ watcher_count }}</div>
    </div>
    <div class="stat-card">
        <div class="stat-label">{{ plugin_t('web_page_stat_loops') }}</div>
        <div class="stat-value">{{ loop_count }}</div>
    </div>
    <div class="stat-card">
        <div class="stat-label">{{ plugin_t('web_page_stat_inline') }}</div>
        <div class="stat-value {{ 'success' if inline_count else 'danger' }}">{{ inline_count }}</div>
    </div>
</div>

<h2 class="section-title">{{ plugin_t('web_page_about') }}</h2>

<div class="card">
    <p class="plugin-intro">{{ plugin_description or plugin_t('web_page_intro') }}</p>

    <div class="info-row">
        <span class="info-label">{{ plugin_t('web_page_author') }}</span>
        <span class="info-value">{{ plugin_author }}</span>
    </div>
    <div class="info-row">
        <span class="info-label">{{ plugin_t('web_page_category') }}</span>
        <span class="info-value">{{ plugin_category }}</span>
    </div>
    <div class="info-row">
        <span class="info-label">{{ plugin_t('web_page_folder') }}</span>
        <span class="info-value">{{ plugin_folder }}</span>
    </div>
    <div class="info-row">
        <span class="info-label">{{ plugin_t('web_page_path_label') }}</span>
        <span class="info-value">{{ request_path }}</span>
    </div>
    <div class="info-row">
        <span class="info-label">{{ plugin_t('web_page_rendered_at') }}</span>
        <span class="info-value">{{ rendered_at }}</span>
    </div>
</div>

<h2 class="section-title">{{ plugin_t('web_page_config') }}</h2>

<div class="card">
    <form id="plugin-config-form"
          data-saving-label="{{ plugin_t('web_page_saving') }}"
          data-saved-label="{{ plugin_t('web_page_saved') }}"
          data-reload-failed-label="{{ plugin_t('web_page_reload_failed') }}"
          autocomplete="off">
        <div class="plugin-form-grid">
            <div class="field">
                <label class="field-label" for="plugin-prefix">{{ plugin_t('web_page_prefix') }}</label>
                <input class="input" id="plugin-prefix" name="prefix" value="{{ prefix }}"
                       placeholder="{{ plugin_t('web_page_prefix_placeholder') }}">
                <span class="field-hint">{{ plugin_t('web_page_prefix_hint') }}</span>
            </div>
            <div class="field">
                <label class="field-label" for="plugin-greeting">{{ plugin_t('web_page_greeting') }}</label>
                <input class="input" id="plugin-greeting" name="greeting" value="{{ greeting }}">
                <span class="field-hint">{{ plugin_t('web_page_greeting_hint') }}</span>
            </div>
            <div class="field">
                <label class="field-label" for="plugin-demo-limit">{{ plugin_t('web_page_demo_limit') }}</label>
                <input class="input" id="plugin-demo-limit" name="demo_limit" type="number"
                       min="0" step="1" value="{{ demo_limit }}">
                <span class="field-hint">{{ plugin_t('web_page_demo_limit_hint') }}</span>
            </div>
        </div>
        <div class="plugin-form-actions">
            <button class="btn btn-primary" type="submit" id="plugin-config-save">
                {{ plugin_t('web_page_save') }}
            </button>
            <span class="plugin-status" id="plugin-config-status" role="status"></span>
        </div>
    </form>
</div>

<h2 class="section-title">{{ plugin_t('web_page_commands') }}</h2>

<div class="card">
    {% if commands %}
    <ul class="plugin-command-list">
        {% for cmd in commands %}
        <li class="plugin-command">
            <div class="plugin-command-main">
                <code class="plugin-command-code">{{ cmd.usage }}</code>
                {% if cmd.description %}
                <span class="plugin-command-desc">{{ cmd.description }}</span>
                {% endif %}
                {% if cmd.aliases %}
                <span class="plugin-command-desc">
                    {{ plugin_t('web_page_aliases') }}: {{ cmd.aliases|join(', ') }}
                </span>
                {% endif %}
            </div>
            <button type="button" class="btn btn-sm plugin-copy"
                    data-copy="{{ cmd.usage }}"
                    data-copied-label="{{ plugin_t('web_page_copied') }}">
                {{ plugin_t('web_page_copy') }}
            </button>
        </li>
        {% endfor %}
    </ul>
    {% else %}
    <p class="empty-text">{{ plugin_t('web_page_no_commands') }}</p>
    {% endif %}
</div>

<h2 class="section-title">{{ plugin_t('web_page_actions') }}</h2>

<div class="card">
    <div class="info-row plugin-api-row">
        <span class="info-label">{{ plugin_t('web_page_api') }}</span>
        <div class="plugin-api-value">
            <code>{{ plugin_api_url }}</code>
            <button type="button" class="btn btn-sm plugin-copy"
                    data-copy="{{ plugin_api_url }}"
                    data-copied-label="{{ plugin_t('web_page_copied') }}">
                {{ plugin_t('web_page_copy') }}
            </button>
        </div>
    </div>
    <div class="plugin-actions-row">
        <button type="button" class="btn" id="plugin-refresh-button">
            {{ plugin_t('web_page_refresh') }}
        </button>
        <span class="plugin-status" id="plugin-action-status" role="status"></span>
    </div>
</div>

<script>
    window.__PLUGIN_DATA__ = {
        name: {{ plugin_name|tojson }},
        version: {{ plugin_version|tojson }},
        staticUrl: {{ static_url|tojson }},
        apiUrl: {{ plugin_api_url|tojson }},
        reloadUrl: {{ plugin_reload_url|tojson }}
    };
</script>
<script src="{{ plugin_static_url }}/script.js?v={{ static_version }}"></script>
{% endblock %}
''',
        PLUGIN_NAME=name,
    )


def build_style() -> str:
    """Builds static/style.css for the generated plugin."""
    return """/* Plugin page additions. Layout, cards, buttons and form fields come from
   the main TeleSender web interface (src/Web/static/style.css). */

.plugin-intro {
    margin: 0 0 16px;
    color: var(--muted);
    line-height: 1.55;
}

.plugin-form-grid {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 14px;
}

.plugin-form-actions,
.plugin-actions-row {
    display: flex;
    align-items: center;
    gap: 12px;
    flex-wrap: wrap;
    margin-top: 8px;
}

.plugin-status {
    font-size: 11.5px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.03em;
    color: var(--muted);
}

.plugin-status.success { color: var(--success); }
.plugin-status.danger { color: var(--accent); }
.plugin-status.muted { color: var(--muted); }

.plugin-command-list {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 10px;
}

.plugin-command {
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 10px 12px;
    padding: 10px 12px;
    border: 2px solid var(--border);
    border-radius: var(--radius);
    background: var(--surface-2);
}

.plugin-command-main {
    display: flex;
    flex: 1 1 220px;
    flex-direction: column;
    gap: 4px;
    min-width: 0;
}

.plugin-command .btn {
    flex: 0 0 auto;
}

.plugin-command-code {
    align-self: flex-start;
    max-width: 100%;
    padding: 2px 6px;
    border: 1px solid var(--border);
    border-radius: var(--radius);
    background: var(--surface);
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    font-size: 13px;
    font-weight: 700;
    overflow-wrap: anywhere;
}

.plugin-command-desc {
    font-size: 12px;
    color: var(--muted);
    overflow-wrap: anywhere;
}

.plugin-api-row {
    flex-direction: column;
    align-items: stretch;
    gap: 8px;
}

.plugin-api-row .info-label {
    flex: none;
}

.plugin-api-value {
    display: flex;
    align-items: center;
    justify-content: flex-start;
    flex-wrap: wrap;
    gap: 8px;
    width: 100%;
    min-width: 0;
    margin-left: 0;
    text-align: left;
}

.plugin-api-value code {
    flex: 0 0 auto;
    min-width: 0;
    max-width: 100%;
    padding: 3px 6px;
    border: 1px solid var(--border);
    border-radius: var(--radius);
    background: var(--surface-2);
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    font-size: 12px;
    text-align: left;
    white-space: normal;
    overflow-wrap: anywhere;
}

.plugin-api-value .btn {
    flex: 0 0 auto;
}

@media (max-width: 800px) {
    .plugin-form-grid {
        grid-template-columns: 1fr;
    }
}
"""


def build_script() -> str:
    """Builds static/script.js for the generated plugin."""
    return """(function () {
    "use strict";

    const data = window.__PLUGIN_DATA__ || {};

    function setStatus(element, text, kind) {
        if (!element) {
            return;
        }
        element.textContent = text || "";
        element.className = "plugin-status" + (kind ? " " + kind : "");
    }

    async function copyText(text) {
        if (navigator.clipboard && navigator.clipboard.writeText) {
            await navigator.clipboard.writeText(text);
            return;
        }
        const area = document.createElement("textarea");
        area.value = text;
        area.setAttribute("readonly", "");
        area.style.position = "fixed";
        area.style.opacity = "0";
        document.body.appendChild(area);
        area.select();
        document.execCommand("copy");
        document.body.removeChild(area);
    }

    document.querySelectorAll("[data-copy]").forEach(function (button) {
        button.addEventListener("click", async function () {
            const value = button.getAttribute("data-copy") || "";
            const copiedLabel = button.getAttribute("data-copied-label") || "OK";
            const originalLabel = button.textContent;
            try {
                await copyText(value);
                button.textContent = copiedLabel;
                window.setTimeout(function () {
                    button.textContent = originalLabel;
                }, 1200);
            } catch (error) {
                setStatus(
                    document.getElementById("plugin-action-status"),
                    String(error && error.message ? error.message : error),
                    "danger"
                );
            }
        });
    });

    const form = document.getElementById("plugin-config-form");
    const formStatus = document.getElementById("plugin-config-status");
    if (form) {
        form.addEventListener("submit", async function (event) {
            event.preventDefault();
            const button = document.getElementById("plugin-config-save");
            const payload = {
                prefix: form.elements.prefix.value,
                greeting: form.elements.greeting.value,
                demo_limit: Number(form.elements.demo_limit.value || 0)
            };

            if (button) {
                button.disabled = true;
            }
            setStatus(
                formStatus,
                form.getAttribute("data-saving-label") || "Saving...",
                "muted"
            );

            try {
                const response = await fetch(data.apiUrl, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ values: payload })
                });
                const result = await response.json().catch(function () {
                    return {};
                });
                if (!response.ok) {
                    throw new Error(result.error || ("HTTP " + response.status));
                }
                if (result.applied !== true && data.reloadUrl) {
                    const reloadResponse = await fetch(data.reloadUrl, {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: "{}"
                    });
                    const reloadResult = await reloadResponse.json().catch(function () {
                        return {};
                    });
                    if (!reloadResponse.ok || reloadResult.success === false) {
                        throw new Error(
                            form.getAttribute("data-reload-failed-label") ||
                            "Saved, but the plugin reload failed"
                        );
                    }
                }
                setStatus(
                    formStatus,
                    form.getAttribute("data-saved-label") || "Saved",
                    "success"
                );
            } catch (error) {
                setStatus(
                    formStatus,
                    String(error && error.message ? error.message : error),
                    "danger"
                );
            } finally {
                if (button) {
                    button.disabled = false;
                }
            }
        });
    }

    const refresh = document.getElementById("plugin-refresh-button");
    if (refresh) {
        refresh.addEventListener("click", function () {
            window.location.reload();
        });
    }
})();
"""


def build_translations(name: str) -> Dict[str, Dict[str, str]]:
    """Builds a lang/*.json dictionary for every supported interface language."""
    translations = {
        "uk": {
            "plugin_loaded": "Плагін {version} завантажено",
            "plugin_unloaded": "Плагін вивантажено",
            "inline_unavailable": "Inline-бот ще не готовий. Спробуйте пізніше.",
            "button_like": "Лайк ({count})",
            "button_alert": "Сповіщення",
            "button_alert_text": "Це приклад кнопки з дією answer.",
            "button_time": "Оновити час",
            "button_close": "Закрити",
            "form_title": "Меню плагіна {name}",
            "form_liked": "Кількість лайків: {count}",
            "like_answer": "Лайк додано! ({count})",
            "time_answer": "Час оновлено!",
            "form_time": "Останнє оновлення: {time}",
            "config_set_usage": "Використання: .{command}_set <key> <value>. Доступні ключі: {keys}",
            "config_set_invalid_key": "Невідомий ключ «{field}». Доступні: {keys}",
            "config_set_invalid_int": "«{field}» має бути цілим числом.",
            "config_set_changed": "Збережено {field}: {old} → {new}",
            "cmd_demo_description": "Демонстраційна команда плагіна",
            "cmd_ping_description": "Виміряти затримку відповіді Telegram",
            "cmd_config_description": "Показати акаунт Telegram і налаштування плагіна",
            "cmd_set_description": "Змінити значення налаштування й зберегти його",
            "cmd_menu_description": "Відкрити демонстраційне inline-меню",
            "config_account": "Акаунт: {value}",
            "config_prefix": "Префікс: {value}",
            "config_greeting": "Привітання: {value}",
            "config_demo_limit": "Ліміт демо: {value}",
            "demo_reply": "{prefix}{greeting} від {name}!",
            "plugin_main_started": "Плагін {name}: main() запущено",
            "config_changed_log": "Конфіг {field}: {old} → {new}",
            "heartbeat_log": "heartbeat о {time}",
            "button_github": "GitHub",
            "inline_result_description": "Згенеровано TeleSender",
            "api_invalid_json": "Некоректний JSON",
            "api_unknown_key": "Невідомий ключ: {field}",
            "api_invalid_int": "«{field}» має бути цілим числом",
            "web_page_title": "Огляд",
            "web_page_intro": f"Цю сторінку надає плагін {name}.",
            "web_page_enabled": "Увімкнено",
            "web_page_disabled": "Вимкнено",
            "web_page_about": "Про плагін",
            "web_page_config": "Налаштування",
            "web_page_commands": "Команди",
            "web_page_actions": "Дії",
            "web_page_stat_commands": "Команди",
            "web_page_stat_watchers": "Спостерігачі",
            "web_page_stat_loops": "Цикли",
            "web_page_stat_inline": "Інлайн",
            "web_page_greeting": "Привітання",
            "web_page_greeting_hint": "Текст, який показує основна команда.",
            "web_page_prefix": "Префікс",
            "web_page_prefix_placeholder": "наприклад, !",
            "web_page_prefix_hint": "Додається перед привітанням основної команди.",
            "web_page_demo_limit": "Ліміт демо",
            "web_page_demo_limit_hint": "Ціле число, яке зберігається для вашої логіки. Демо-команда його не використовує.",
            "web_page_author": "Автор",
            "web_page_category": "Категорія",
            "web_page_folder": "Тека",
            "web_page_path_label": "Шлях",
            "web_page_api": "API",
            "web_page_rendered_at": "Відрендерено",
            "web_page_save": "Зберегти",
            "web_page_saving": "Збереження…",
            "web_page_saved": "Збережено",
            "web_page_reload_failed": "Збережено, але плагін не перезавантажився",
            "web_page_copy": "Копіювати",
            "web_page_copied": "Скопійовано",
            "web_page_refresh": "Оновити сторінку",
            "web_page_aliases": "Аліаси",
            "web_page_no_commands": "Команди не зареєстровані.",
            "ping_measuring": "🏓 Вимірюю затримку…",
            "ping_result": "🏓 pong — {ms} мс",
            "watcher_demo_reply": "👀 {name}: watcher отримав «{text}»",
            "watcher_regex_reply": "👀 {name}: regex watcher отримав «{text}»",
            "menu_overview": "Огляд",
        },
        "en": {
            "plugin_loaded": "Plugin {version} loaded",
            "plugin_unloaded": "Plugin unloaded",
            "inline_unavailable": "The inline bot is not ready yet. Try again later.",
            "button_like": "Like ({count})",
            "button_alert": "Alert",
            "button_alert_text": "This is an example of the answer action.",
            "button_time": "Refresh time",
            "button_close": "Close",
            "form_title": "{name} plugin menu",
            "form_liked": "Likes: {count}",
            "like_answer": "Like added! ({count})",
            "time_answer": "Time refreshed!",
            "form_time": "Last update: {time}",
            "config_set_usage": "Usage: .{command}_set <key> <value>. Available keys: {keys}",
            "config_set_invalid_key": "Unknown key “{field}”. Available: {keys}",
            "config_set_invalid_int": "“{field}” must be an integer.",
            "config_set_changed": "Saved {field}: {old} → {new}",
            "cmd_demo_description": "Plugin demo command",
            "cmd_ping_description": "Measure the Telegram round-trip latency",
            "cmd_config_description": "Show the Telegram account and plugin configuration",
            "cmd_set_description": "Change a config value and save it",
            "cmd_menu_description": "Open the inline demo menu",
            "config_account": "Account: {value}",
            "config_prefix": "Prefix: {value}",
            "config_greeting": "Greeting: {value}",
            "config_demo_limit": "Demo limit: {value}",
            "demo_reply": "{prefix}{greeting} from {name}!",
            "plugin_main_started": "Plugin {name}: main() started",
            "config_changed_log": "Config {field}: {old} → {new}",
            "heartbeat_log": "heartbeat at {time}",
            "button_github": "GitHub",
            "inline_result_description": "Generated by TeleSender",
            "api_invalid_json": "Invalid JSON",
            "api_unknown_key": "Unknown key: {field}",
            "api_invalid_int": "“{field}” must be an integer",
            "web_page_title": "Overview",
            "web_page_intro": f"This page is provided by the {name} plugin.",
            "web_page_enabled": "Enabled",
            "web_page_disabled": "Disabled",
            "web_page_about": "About plugin",
            "web_page_config": "Configuration",
            "web_page_commands": "Commands",
            "web_page_actions": "Actions",
            "web_page_stat_commands": "Commands",
            "web_page_stat_watchers": "Watchers",
            "web_page_stat_loops": "Loops",
            "web_page_stat_inline": "Inline",
            "web_page_greeting": "Greeting",
            "web_page_greeting_hint": "Text shown by the main command.",
            "web_page_prefix": "Prefix",
            "web_page_prefix_placeholder": "for example, !",
            "web_page_prefix_hint": "Added before the greeting of the main command.",
            "web_page_demo_limit": "Demo limit",
            "web_page_demo_limit_hint": "Integer stored for your own logic. The demo command does not use it.",
            "web_page_author": "Author",
            "web_page_category": "Category",
            "web_page_folder": "Folder",
            "web_page_path_label": "Path",
            "web_page_api": "API",
            "web_page_rendered_at": "Rendered at",
            "web_page_save": "Save",
            "web_page_saving": "Saving…",
            "web_page_saved": "Saved",
            "web_page_reload_failed": "Saved, but the plugin reload failed",
            "web_page_copy": "Copy",
            "web_page_copied": "Copied",
            "web_page_refresh": "Reload page",
            "web_page_aliases": "Aliases",
            "web_page_no_commands": "No commands registered.",
            "ping_measuring": "🏓 Measuring latency…",
            "ping_result": "🏓 pong — {ms} ms",
            "watcher_demo_reply": "👀 {name}: watcher received “{text}”",
            "watcher_regex_reply": "👀 {name}: regex watcher received “{text}”",
            "menu_overview": "Overview",
        },
        "ru": {
            "plugin_loaded": "Плагин {version} загружен",
            "plugin_unloaded": "Плагин выгружен",
            "inline_unavailable": "Inline-бот ещё не готов. Попробуйте позже.",
            "button_like": "Лайк ({count})",
            "button_alert": "Уведомление",
            "button_alert_text": "Это пример кнопки с действием answer.",
            "button_time": "Обновить время",
            "button_close": "Закрыть",
            "form_title": "Меню плагина {name}",
            "form_liked": "Количество лайков: {count}",
            "like_answer": "Лайк добавлен! ({count})",
            "time_answer": "Время обновлено!",
            "form_time": "Последнее обновление: {time}",
            "config_set_usage": "Использование: .{command}_set <key> <value>. Доступные ключи: {keys}",
            "config_set_invalid_key": "Неизвестный ключ «{field}». Доступные: {keys}",
            "config_set_invalid_int": "«{field}» должно быть целым числом.",
            "config_set_changed": "Сохранено {field}: {old} → {new}",
            "cmd_demo_description": "Демонстрационная команда плагина",
            "cmd_ping_description": "Измерить задержку ответа Telegram",
            "cmd_config_description": "Показать аккаунт Telegram и настройки плагина",
            "cmd_set_description": "Изменить значение настройки и сохранить его",
            "cmd_menu_description": "Открыть демонстрационное inline-меню",
            "config_account": "Аккаунт: {value}",
            "config_prefix": "Префикс: {value}",
            "config_greeting": "Приветствие: {value}",
            "config_demo_limit": "Лимит демо: {value}",
            "demo_reply": "{prefix}{greeting} от {name}!",
            "plugin_main_started": "Плагин {name}: main() запущен",
            "config_changed_log": "Конфиг {field}: {old} → {new}",
            "heartbeat_log": "heartbeat в {time}",
            "button_github": "GitHub",
            "inline_result_description": "Сгенерировано TeleSender",
            "api_invalid_json": "Некорректный JSON",
            "api_unknown_key": "Неизвестный ключ: {field}",
            "api_invalid_int": "«{field}» должно быть целым числом",
            "web_page_title": "Обзор",
            "web_page_intro": f"Эту страницу предоставляет плагин {name}.",
            "web_page_enabled": "Включён",
            "web_page_disabled": "Выключен",
            "web_page_about": "О плагине",
            "web_page_config": "Настройки",
            "web_page_commands": "Команды",
            "web_page_actions": "Действия",
            "web_page_stat_commands": "Команды",
            "web_page_stat_watchers": "Наблюдатели",
            "web_page_stat_loops": "Циклы",
            "web_page_stat_inline": "Инлайн",
            "web_page_greeting": "Приветствие",
            "web_page_greeting_hint": "Текст, который показывает основная команда.",
            "web_page_prefix": "Префикс",
            "web_page_prefix_placeholder": "например, !",
            "web_page_prefix_hint": "Добавляется перед приветствием основной команды.",
            "web_page_demo_limit": "Лимит демо",
            "web_page_demo_limit_hint": "Целое число, которое хранится для вашей логики. Демо-команда его не использует.",
            "web_page_author": "Автор",
            "web_page_category": "Категория",
            "web_page_folder": "Папка",
            "web_page_path_label": "Путь",
            "web_page_api": "API",
            "web_page_rendered_at": "Отрисовано",
            "web_page_save": "Сохранить",
            "web_page_saving": "Сохранение…",
            "web_page_saved": "Сохранено",
            "web_page_reload_failed": "Сохранено, но плагин не перезагрузился",
            "web_page_copy": "Копировать",
            "web_page_copied": "Скопировано",
            "web_page_refresh": "Обновить страницу",
            "web_page_aliases": "Алиасы",
            "web_page_no_commands": "Команды не зарегистрированы.",
            "ping_measuring": "🏓 Измеряю задержку…",
            "ping_result": "🏓 pong — {ms} мс",
            "watcher_demo_reply": "👀 {name}: watcher получил «{text}»",
            "watcher_regex_reply": "👀 {name}: regex watcher получил «{text}»",
            "menu_overview": "Обзор",
        },
    }

    for code, values in PLUGIN_TRANSLATIONS.items():
        translations[code] = {
            key: value.format(name=name) if key == "web_page_intro" else value
            for key, value in values.items()
        }

    return translations


def build_readme(name: str, author: str, description: str, category: str,
                 command_name: str, class_name: str) -> str:
    """Builds the full developer README for the generated plugin."""
    return _render(
        '''# __PLUGIN_NAME__

__DESCRIPTION__

- **Author:** __AUTHOR__
- **Category:** __CATEGORY__
- **Version:** 1.0.0
- **Plugin folder:** `plugins/__PLUGIN_NAME__`
- **Class:** `__CLASS_NAME__Plugin`
- **Main command:** `.__COMMAND_NAME__`

This plugin was generated by TeleSender's **Create plugin** tool. It is both a
working example and a reference for the plugin API implemented in `core/`.

> Every API described below is backed by the TeleSender source code at the
> time of generation. Features that are registered but not dispatched by the
> current runtime are explicitly marked as limitations.

---

## 1. File structure

```
plugins/__PLUGIN_NAME__/
├── __init__.py          # optional package marker
├── manifest.json        # required metadata
├── config.json          # default configuration
├── plugin.py            # plugin class and API examples
├── README.md            # this file
├── lang/                # plugin translations (13 interface languages)
│   ├── uk.json          # Ukrainian
│   ├── en.json          # English fallback
│   ├── ru.json          # Russian
│   ├── pl.json          # Polish
│   ├── de.json          # German
│   ├── es.json          # Spanish
│   ├── fr.json          # French
│   ├── pt.json          # Portuguese
│   ├── tr.json          # Turkish
│   ├── id.json          # Indonesian
│   ├── it.json          # Italian
│   ├── ja.json          # Japanese
│   └── ko.json          # Korean
├── templates/
│   └── index.html       # Jinja template for the web page
└── static/
    ├── style.css        # plugin CSS
    └── script.js        # plugin JavaScript
```

The loader requires `manifest.json` and `plugin.py`. `__init__.py` is not
required by the loader, but it is generated so the folder is a normal package.
`templates/` and `static/` are always generated; they are only used when
`plugin.py` contains an `@page` definition.

---

## 2. Loading lifecycle

1. `PluginLoader.find_plugins()` looks for folders containing both
   `manifest.json` and `plugin.py`.
2. The manifest is validated. Required fields: `name`, `author`, `version`.
3. `config.json` is loaded as the default configuration.
4. The class is imported from `plugin.py`; the first `Plugin` subclass found in
   the module is instantiated. Define exactly one plugin class per `plugin.py`
   to avoid ambiguity.
5. `on_load()` is awaited.
6. `main()` is awaited.
7. `@loop` tasks are started.
8. `_loaded` is set to `True`.
9. `@page` definitions are registered in the page registry.
10. On unload/reload: `on_unload()` is awaited, loop tasks are cancelled,
    pages and handlers are unregistered, and the module is removed from
    `sys.modules`. This lookup uses the folder name, so keep the manifest
    `name` equal to the folder name (see section 3).

`main()` is abstract in the base class and must be implemented, even if it is
empty.

### Generated demo commands and watchers

The generated `plugin.py` registers working examples:

| Handler | Behavior |
|---|---|
| `.__COMMAND_NAME__` (alias `.np`) | Replies with `prefix + greeting + " from <name>!"`. |
| `.__COMMAND_NAME___ping` | Sends a placeholder, measures the Telegram round-trip with `time.perf_counter()`, then edits that message to `🏓 pong — N ms`. |
| `.__COMMAND_NAME___config` | Sends one message with the Telegram account, `prefix`, `greeting` and `demo_limit`. |
| `.__COMMAND_NAME___set <key> <value>` | Changes `prefix`, `greeting` or `demo_limit` from Telegram and saves immediately. |
| `.__COMMAND_NAME___menu` | Shows an inline form with buttons; the Like button keeps a live counter in both its label and the form text; requires a ready inline bot. |

The `.np` alias is only a demo. Aliases share one global namespace: if two
loaded plugins register the same alias, the plugin loaded last wins. Rename or
remove the alias when you copy the scaffold for another plugin.

The generated watchers reply in Telegram instead of only writing logs:

- `demo_watcher` replies to any message that contains the plugin command name.
- `regex_watcher` replies to group messages that start with `!demo`.

Both watchers ignore outgoing replies (`event.out and event.is_reply`) so their
own replies and command replies cannot trigger them again. A normal outgoing
message from the owner is still handled, which is useful because a userbot
shares the owner's Telegram account. Edit or remove the demo watchers before
using the plugin in busy chats.

---

## 3. manifest.json

```json
{
  "name": "__PLUGIN_NAME__",
  "author": "__AUTHOR__",
  "version": "1.0.0",
  "description": "__DESCRIPTION__",
  "category": "__CATEGORY__",
  "id": "__PLUGIN_ID__",
  "requirements": [],
  "icon": "puzzle"
}
```

| Field | Required | Purpose | Default / notes |
|---|---|---|---|
| `name` | yes | Display name and registry key | — |
| `author` | yes | Author name | — |
| `version` | yes | Version string | `"0.1"` in the base class if missing, but the loader requires the field |
| `description` | no | Short description | `"No description"` |
| `category` | no | Group shown in plugin lists | `"General"` |
| `id` | no | Unique plugin id | Generated as `f"{name.lower()}.{author.lower().replace(' ', '_')}"` |
| `requirements` | no | Informational dependency list; TeleSender does **not** install these packages automatically | `[]` |
| `icon` | no | Fallback navigation icon | Lucide name, emoji, URL, or `static/` image |
| `repository` / `source` | no | Git URL used by the updater | No automatic update without it |

The loader warns when `id` differs from the generated id. Keep them equal.

**Manifest name vs folder name:** routes, static files, pages, loader
instances and handler registrations use the **folder name**. The plugin
registry uses the manifest `name`. They are often different
(`gemini` vs `GeminiAI`). When interacting with another plugin, use
`self.bot.plugin_service.get_plugin_instance("<folder_name>")`.

Keep `manifest["name"]` equal to the folder name. Several loader and registry
lookups (including unload hooks, loop cancellation and inline handler lookup)
assume the same key, so a mismatch can leave stale registry entries and
uncancelled loop tasks behind.

---

## 4. config.json and configuration API

`config.json` contains default values:

```json
{
  "_version": "1.0.0",
  "enabled": true,
  "prefix": "",
  "greeting": "Hello",
  "demo_limit": 10
}
```

`_version` is a service field. It is removed from the defaults and written back
as the plugin version.

`demo_limit` is an example value that is stored and shown on the plugin page,
but the generated demo command does not use it. It is there to show how to add
and persist your own settings.

### `self.get_config(key, default=None)`

Returns a configuration value. Saved database values override `config.json`
defaults.

### `self.set_config(key, value) -> bool`

Sets a value and saves the configuration immediately.

- Returns `False` if `key` is not present in the defaults loaded from
  `config.json`.
- Returns `True` after saving.
- It does **not** call `on_config_change()` automatically.

```python
if self.set_config("greeting", "Hi"):
    self.logger.info("greeting saved")
```

### Changing config from Telegram

The generated plugin includes a working setter command:

```text
.__COMMAND_NAME___set greeting Hi there
.__COMMAND_NAME___set prefix !
.__COMMAND_NAME___set demo_limit 20
```

It accepts only `prefix`, `greeting` and `demo_limit`, converts `demo_limit`
to `int`, calls `set_config()` (the change is immediate and persisted) and
replies with the old and new value. Extend the `allowed` tuple when you add
new keys. Like every command, it is available only to `allowed_users`.

### `self.save_config()`

Writes `self._config_data` to the shared database table `plugin_config`, keyed
by `plugin_id`. You normally do not need to call it directly because
`set_config()` already saves.

### Configuration storage

- Defaults: `config.json`.
- User values: database table `plugin_config` (`id`, `config` JSON,
  `updated_at`).
- On load, only keys that exist in the defaults are copied from the database.
- On plugin update, TeleSender merges the old config, the new `config.json`
  and the database values, then updates `_version`.
- `self._config_data` is the effective dictionary. It is available, but treat
  it as internal; prefer `get_config()` / `set_config()`.

### `on_config_change(key, old_value, new_value)`

The base class defines this hook, but the current runtime never calls it. It is
shown in `plugin.py` as documentation only. If you need change notifications,
call it yourself after `set_config()`.

### Configuration through the web interface

TeleSender does not ship a generic plugin-config form. A plugin can:

1. Read config in a page handler with `self.get_config(...)` and render it.
2. Save config through its own POST route registered with
   `register_web_api_routes(app)` (see section 9).
3. Use a GET query parameter for simple, idempotent settings, but avoid
   mutating state on GET in production.

---

## 5. Plugin base class API

Import the base class with:

```python
from core import Plugin
```

### Available attributes

| Attribute | Meaning |
|---|---|
| `self.client` | Telethon `TelegramClient` of the user account |
| `self.db` | Shared `DatabaseService` instance, or `None` |
| `self.bot` | TeleSender `Bot` instance |
| `self.inline` | `InlineManager` or `None`; injected by the loader |
| `self.logger` | `logging.Logger` named `plugin.<ClassName>` |
| `self.path` | Path to the plugin folder |
| `self.folder_name` | Folder name (used in routes and loader instances) |
| `self.plugin_id` | Manifest `id` |
| `self.name` | Manifest `name` |
| `self.author` | Manifest `author` |
| `self.version` | Manifest `version` |
| `self.description` | Manifest `description` |
| `self.category` | Manifest `category` |
| `self.requirements` | Manifest `requirements` |
| `self._manifest` | Raw manifest dictionary |
| `self._config_data` | Effective configuration dictionary |
| `self._localization` | `PluginLocalization` instance, or `None` |

### `self.get_manifest()`

Returns a dictionary with `id`, `name`, `author`, `version`, `description`,
`category` and `requirements`. Extra manifest fields such as `icon` or
`repository` are not included; read `self._manifest` for those.

### `self.t(key, **kwargs)`

Returns a translation from `lang/*.json`. Supports flat keys and dot-separated
nested keys. Missing keys fall back to English, then to the key itself.
Formatting uses Python `str.format(**kwargs)`.

```python
await event.reply(self.t("hello", name=event.sender_id))
```

Language changes are propagated to loaded plugins by
`PluginService.set_language()`.

### Lifecycle hooks

| Hook | Signature | Called by |
|---|---|---|
| `on_load` | `async def on_load(self)` | Loader, before `main()` |
| `main` | `async def main(self)` | Loader, after `on_load()` |
| `on_unload` | `async def on_unload(self)` | Loader, before unregistering |
| `on_page_render` | `def/async def on_page_render(self, page_path, request)` | Web page renderer, for every plugin page |
| `on_config_change` | `async def on_config_change(self, key, old_value, new_value)` | **Not called by the runtime** |
| `get_menu_items` | `def get_menu_items(self)` | Web navigation/plugin page metadata; must be sync |

### Handler/loop helper methods

The base class exposes `register_handler()`, `register_loop()`,
`get_handlers()`, `get_loop_tasks()`, `add_loop_task()` and
`clear_loop_tasks()`. The loader uses some of them internally, but it does
**not** scan `self.get_handlers()` for handlers added dynamically. Register
commands, watchers, inline handlers, callbacks and loops with decorators.

---

## 6. Decorators

All decorators are exported from `core`:

```python
from core import command, watcher, callback, inline, loop, page
```

### 6.1 `@command`

```python
@command(
    command="demo",          # None -> function name
    aliases=["d", "demo2"],  # default: []
    description="Demo",      # default: ""
    usage=".demo",           # default: ""
    hide=False,              # default: False
)
async def demo(self, event):
    ...
```

| Parameter | Allowed values | Default |
|---|---|---|
| `command` | command name without the dot, or `None` | function name |
| `aliases` | list of strings | `[]` |
| `description` | string | `""` |
| `usage` | string | `""` |
| `hide` | `True` / `False` | `False` |

Behavior:

- Commands use the **`.` prefix**. `.demo`, `.demo@botname` and aliases work.
- Command names and aliases must be lowercase. The runtime lowercases the
  incoming command before lookup; it does not normalize the registered name.
- Plugin commands are checked before built-in commands.
- Handler signature is `(self, event)`; the runtime calls
  `handler(plugin_instance, event)`.
- Exceptions are caught by `CommandHandler`; the user receives the translated
  `messages.debug_command_error` message.
- The `description`, `usage` and `hide` values are stored in the registry, but
  the built-in `.help` command currently uses a static text and does not read
  plugin command metadata. Do not rely on `hide` to hide anything yet.

### 6.2 `@watcher`

```python
@watcher(
    pattern="hello",         # default: None
    regex=False,             # default: False
    only_private=False,      # default: False
    only_groups=False,       # default: False
    from_users=None,         # default: None
)
async def on_message(self, event):
    ...
```

| Parameter | Allowed values | Default |
|---|---|---|
| `pattern` | substring or regex; `None` matches all messages | `None` |
| `regex` | `True` / `False` | `False` |
| `only_private` | `True` / `False` | `False` |
| `only_groups` | `True` / `False` | `False` |
| `from_users` | list of Telegram user ids | `None` (`[]`) |

Behavior:

- Watchers receive `NewMessage` events only.
- They run only when no command handled the message.
- `pattern` is a substring match by default; with `regex=True` it uses
  `re.search(pattern, text)`.
- `only_private` and `only_groups` filter by chat type.
- `from_users` filters by `event.sender_id`.
- `config.allowed_users` is checked before watchers run.
- Handler signature is `(self, event)`.
- Exceptions are logged; they do not stop other watchers.
- The generated demo watchers call `event.reply(...)` and skip outgoing
  replies (`event.out and event.is_reply`). Keep that guard when your reply
  text can match your own watcher pattern, otherwise the bot can answer itself
  in a loop. Outgoing non-reply messages are still handled, because a userbot
  shares the owner's Telegram account.

### 6.3 `@loop`

```python
@loop(interval=60.0, start_immediately=False)
async def tick(self):
    ...
```

| Parameter | Allowed values | Default |
|---|---|---|
| `interval` | seconds, int/float | `60.0` |
| `start_immediately` | `True` / `False` | `False` |

Behavior:

- The handler is called with the plugin instance, so define it as an instance
  method with `self`.
- With `start_immediately=False` the first run happens after `interval`.
- With `start_immediately=True` it runs once immediately, then waits.
- Exceptions are logged and the loop continues.
- Tasks are cancelled on unload/reload.
- Keep the handler short. Use `asyncio.sleep` for waits and avoid blocking the
  event loop.

### 6.4 `@inline`

```python
from core import inline

@inline("demo")
async def inline_query(self, event):
    if not event.text.startswith("demo "):
        return []
    return [event.builder.article(
        title="Demo",
        text="<b>Hello</b>",
        description="Generated by the plugin",
        parse_mode="html",
    )]
```

| Parameter | Allowed values | Default |
|---|---|---|
| `inline_id` | registration key, or `None` | function name |

Behavior:

- Handlers are called for every inline query that is **not** an active inline
  form.
- `inline_id` is only a dictionary key. The runtime does not filter by it;
  filter `event.text` yourself.
- All registered inline handlers are invoked and their returned lists are
  combined.
- Return a list of Telethon inline results (for example
  `event.builder.article(...)`). The first 50 results are sent.
- If no handler returns results, a default "TeleSender Assistant" result is
  shown.
- Only authorized users (owner / `config.allowed_users`) can use the inline
  bot.
- Errors are logged per handler.

### 6.5 `@callback`

```python
from core import callback

@callback("my_button")
async def on_button(self, call):
    ...
```

| Parameter | Allowed values | Default |
|---|---|---|
| `callback_id` | registration key, or `None` | function name |

`@callback` registers a handler in `PluginRegistry._callbacks`, but the current
runtime has no callback-query dispatcher that calls `get_callback()`. The
decorator is therefore **not functional** at the moment. For real inline
button callbacks, use `self.inline.form(...)` with button dictionaries and a
`callback` function (section 7).

### 6.6 `@page`

```python
from core import page

@page(
    path="overview",             # required, relative URL below /plugin/<folder>/
    template="index.html",       # required, relative to templates/
    context=None,                # optional static dict
    title=None,                  # optional default title
    title_key=None,              # optional plugin translation key
    icon=None,                   # optional navigation icon
    cache_ttl=None,              # None = global TTL, 0 = no cache
)
async def overview(self, request):
    return {"value": 1}
```

| Parameter | Allowed values | Default |
|---|---|---|
| `path` | non-empty URL path; no backslash, `.`, `..`, or `static/` prefix | required |
| `template` | relative path below `templates/`; no absolute path or `..` | required |
| `context` | mapping or `None` | `None` |
| `title` | string or `None` | `None` |
| `title_key` | plugin translation key or `None` | `None` |
| `icon` | Lucide name, emoji, URL, `static/` file, inline SVG, or `None` | `None` |
| `cache_ttl` | non-negative number or `None` | `None` |

Behavior:

- URL: `/plugin/<folder_name>/<path>`.
- The page is available only while the plugin is loaded.
- The handler may be sync or async. Sync handlers run in a worker thread.
- Return a `dict` to extend the template context, a `web.StreamResponse` to
  return a custom response, a string to set the `content` context value, or
  `None` to keep the context unchanged.
- If the handler raises, the response is HTTP 500 and the error is logged.
- The page title is resolved from `title_key` via `self.t()`, then `title`,
  then the path.
- `cache_ttl=None` uses the global web page cache TTL. `0` disables caching
  for the page. Positive values set the per-page TTL.

---

## 7. Inline forms and buttons

`self.inline` is an `InlineManager` injected by the loader. It may be `None` if
the inline bot could not start. Always check:

```python
if not self.inline or not self.inline.is_ready:
    await event.reply(self.t("inline_unavailable"))
    return
```

### `await self.inline.form(...)`

```python
await self.inline.form(
    text="<b>Menu</b>",
    message=event,
    reply_markup=[
        [{"text": "Like", "callback": self.on_like, "args": [0]}],
        [{"text": "Close", "action": "close"}],
    ],
    force_me=True,
    ttl=300,
)
```

| Parameter | Allowed values | Default |
|---|---|---|
| `text` | HTML string | required |
| `message` | Telethon event/message used to resolve chat and owner | required |
| `reply_markup` | dict, 1D list, or 2D list of button dicts | `None` |
| `force_me` | `True` / `False` | `True` |
| `ttl` | seconds or `None` | `None` |
| `disable_security` | `True` / `False` | `False` |
| `always_allow` | list of user ids or `None` | `None` |
| `reply_to` | message/id or `None` | `None` |
| `silent` | accepted but currently unused | `False` |

Returns an `InlineForm` or `None` if the inline system is not ready.

Behavior:

- The triggering command message is deleted when possible.
- The form is first sent through an inline query and falls back to a direct bot
  message if inline mode is not allowed in the chat.
- `force_me=True` restricts button presses to the command author.
- `disable_security=True` disables the owner check; use carefully.
- `always_allow` adds user ids that may press buttons.
- `ttl` expires the form; the cleanup loop removes expired forms about once a
  minute and deletes their message when possible.
- `silent` is accepted by the signature but not used by the implementation.

### Button dictionary fields

| Field | Purpose | Default |
|---|---|---|
| `text` | Button label | `"Button"` |
| `callback` | `async def handler(call, *args, **kwargs)` | `None` |
| `args` | positional args passed to `callback` | `[]` |
| `kwargs` | keyword args passed to `callback` | `{}` |
| `url` | URL button | `None` |
| `action` | `"close"`, `"unload"`, `"answer"` | `None` |
| `message` | text for `action="answer"` | button text |
| `show_alert` | modal alert instead of toast for `answer` | `False` |
| `input` | switch-inline query text | `None` |
| `button_id` | internal token | generated |

Button priority: `url` -> `input` -> `action` -> `callback`. If no action or
callback is set, the press is simply acknowledged.

### `InlineCall`

The callback receives an `InlineCall` with:

- `call.event` — raw Telethon `CallbackQuery.Event`
- `call.form` — `InlineForm`
- `call.button` — pressed `ButtonDefinition`
- `call.form_id`, `call.sender_id`, `call.data`

Methods:

| Method | Purpose |
|---|---|
| `await call.edit(text=None, reply_markup=None)` | Edit text and/or buttons; existing buttons are kept when `reply_markup` is `None` |
| `await call.delete()` | Delete the form message and remove the form |
| `await call.answer(text=None, show_alert=False)` | Acknowledge the press, optionally with a popup |
| `await call.unload()` | Remove the form from memory without deleting the message |

`call.edit(text, reply_markup=...)` replaces the buttons only when
`reply_markup` is passed. If you change only the text, the button labels stay
the same. To show a live counter on a button, rebuild the markup with the new
label and `args` on every press (see the generated `_menu_markup()` helper).

---

## 8. Web pages: routes, templates and data

### Route

`@page(path="overview", ...)` is served at:

```
/plugin/<folder_name>/overview
```

The generic plugin route is **GET-only**:

```
GET /plugin/{plugin_name}/{page_path:.*}
GET /plugin/{plugin_name}/static/{file_path:.*}
```

For POST/PUT/DELETE, register your own routes with
`register_web_api_routes(app)` (section 9).

### Template context

The page renderer provides:

| Key | Meaning |
|---|---|
| `plugin_instance` | Plugin instance |
| `plugin` | Same instance (alias) |
| `request` | aiohttp request |
| `config` | **Global** TeleSender config, not plugin config |
| `t` | Core translator |
| `plugin_t` | Plugin translator (`self.t`) |
| `page` | `PageDefinition` |
| `page_path` | Page path |
| `plugin_name` | Folder name |
| `plugin_title` | Manifest display name |
| `plugin_static_url` | `/plugin/<folder>/static` |
| `current_path` | Request path |
| `page_title` | Resolved page title |
| `title_key` | Resolved page title (not the translation key) |
| `active` | `plugin:<folder>:<path>` for navigation highlighting |

Values returned by the page handler and `on_page_render()` are merged into the
context. The framework keys above are applied last, so they cannot be
overridden — except `page_title`, `title_key` and `active`, which use
`setdefault()` and can be overridden by a handler.

**Plugin config is not `config`.** Use `plugin_instance.get_config(...)` or
`self.get_config(...)`.

The generated `overview` handler adds its own values on top of the framework
context:

| Key | Meaning |
|---|---|
| `plugin_version`, `plugin_author`, `plugin_category` | Manifest metadata |
| `plugin_description`, `plugin_folder` | Manifest description and folder name |
| `plugin_api_url` | `/plugin/<folder>/api/config` |
| `prefix`, `greeting`, `demo_limit`, `enabled` | Current plugin config |
| `commands` | List of `{name, description, usage, aliases}` dicts |
| `command_count`, `watcher_count`, `loop_count`, `inline_count` | Handler counts |
| `request_path`, `static_url`, `static_version` | Request path, static base URL, and file-mtime cache buster |

The generated page uses these values for the statistics cards, the info card,
the config form, the command list and the copy buttons. It reuses the main web
interface classes (`page-header`, `grid grid-4`, `stat-card`, `card`,
`info-row`, `field`, `input`, `btn`, `badge`, `status-dot`) so it matches the
rest of TeleSender.

### Templates

- Put templates in `plugins/<folder>/templates/`.
- `{% extends "base.html" %}` works because the plugin template loader is part
  of the Jinja `ChoiceLoader`.
- `base.html` currently defines only the `content` block. A `{% block head %}`
  in a plugin template is silently ignored. Put `<link>` and `<script>` tags
  inside `{% block content %}` (as the generated template does).
- Template paths are validated and cannot escape the `templates/` folder.

### Static files

- Put CSS/JS/images in `plugins/<folder>/static/`.
- URL: `/plugin/<folder>/static/<path>`.
- The `plugin_static_url` context variable already points to that base URL.
- Use a file-mtime cache buster such as `?v={{ static_version }}` (the generated
  page computes it from the newest file in `static/`) or your plugin version.
- Static files are served only while the plugin is loaded; path traversal is
  blocked.

### Python -> JavaScript data

Render JSON into the page:

```html
<script>
    window.__PLUGIN_DATA__ = {
        name: {{ plugin_name|tojson }},
        version: {{ plugin_version|tojson }}
    };
</script>
<script src="{{ plugin_static_url }}/script.js"></script>
```

Then read it in `static/script.js`. For dynamic data, call a plugin API route
with `fetch()` and return `web.json_response(...)`.

---

## 9. Extra web API routes

Implement a **synchronous** method:

```python
def register_web_api_routes(self, app):
    app.router.add_post(
        f"/plugin/{self.folder_name}/api/config",
        self._web_save_config,
    )

async def _web_save_config(self, request):
    from aiohttp import web
    data = await request.json()
    values = data.get("values")
    if not isinstance(values, dict):
        values = {str(data.get("key") or ""): data.get("value")}

    allowed = ("prefix", "greeting", "demo_limit")
    cleaned = {}
    for key, value in values.items():
        if key not in allowed:
            return web.json_response(
                {"error": self.t("api_unknown_key", field=key)}, status=400
            )
        if key == "demo_limit":
            try:
                value = int(value)
            except (TypeError, ValueError):
                return web.json_response(
                    {"error": self.t("api_invalid_int", field=key)}, status=400
                )
        cleaned[key] = value

    # The route was registered at startup, so ``self`` may be an old instance
    # after a plugin reload. Save on the instance that is loaded right now.
    target = self
    applied = True
    service = getattr(self.bot, "plugin_service", None) if self.bot else None
    if service is not None:
        current = service.get_plugin_instance(self.folder_name)
        if current is not None:
            target = current
        else:
            applied = False

    for key, value in cleaned.items():
        target.set_config(key, value)
    return web.json_response({
        "ok": True,
        "saved": sorted(cleaned),
        "applied": applied,
        "config": target._config_data,
    })
```

The generated route accepts either a single `{"key": ..., "value": ...}` pair or
a bulk `{"values": {...}}` object. Supported keys are `prefix`, `greeting` and
`demo_limit`; `demo_limit` must be an integer. `self.set_config()` updates the
in-memory value immediately and persists it to the database.

The route resolves the currently loaded plugin instance before saving. This is
required because `register_web_api_routes()` is called once when the web server
starts and the registered bound method keeps pointing to the instance that was
loaded at that moment. After a plugin reload, saving through the old bound
method would update only the old instance, while commands would keep reading
stale values from the new one.

The response contains `applied`: `true` when the currently loaded instance was
updated. The generated page checks this flag; when it is not `true`, it calls
`POST /api/plugins/<folder>/reload` so the change takes effect immediately
without restarting the bot. On a freshly started bot the flag is already
`true`, so the extra reload is skipped.

Behavior and limitations:

- The web server calls `register_web_api_routes(app)` once at startup for every
  plugin that is already loaded.
- If you load the plugin later from the console or web UI, the method is not
  called until the bot/web server restarts.
- The method must be synchronous; an `async def` would return a coroutine that
  the server does not await.
- All routes are behind the normal web authentication middleware.
- Use a distinct path under `/plugin/<folder>/`; the generic page route only
  handles GET, so POST routes do not conflict.

---

## 10. Bottom navigation and icons

### `get_menu_items()`

```python
def get_menu_items(self):
    return [{
        "name": self.t("menu_overview"),
        "url": "overview",      # relative page path
        "icon": "puzzle",       # optional
    }]
```

- Must be synchronous. Returning a coroutine is not supported.
- Each item requires `name` and `url`.
- `url` may be a relative page path (converted to
  `/plugin/<folder>/<path>`) or an absolute path starting with `/`.
- Unsafe URLs (`://`, `//`, `javascript:`, `data:`, `..`) are skipped.
- Exceptions are logged and the plugin's menu items are skipped.

**Important:** `get_menu_items()` does **not** add arbitrary links to the
bottom navigation. A page must be pinned from the `/plugins` page (the pin
button calls `/api/plugin-pages/pin`). Pinned entries use the page title as the
label; `get_menu_items()` is used to associate icons with page URLs.

### Icon formats

The `icon` value may be:

| Form | Example |
|---|---|
| Lucide icon name | `"sparkles"` |
| Emoji / non-ASCII text | `"🚀"` |
| Absolute URL or path | `"/assets/logo.svg"` |
| File inside `static/` | `"icon.svg"` or `"static/icon.svg"` |
| Inline SVG | `"<svg ...>...</svg>"` |

Resolution order for pinned pages:

1. `get_menu_items()` icon for the page URL
2. `@page(icon=...)`
3. `manifest.json` `"icon"`
4. Default pin icon

### Active page highlighting

The page context sets:

```python
active = f"plugin:{plugin_name}:{page_path}"
```

The bottom navigation marks an item active when `active == item.active_key` or
`request.path == item.path`. You normally do not need to do anything; the
framework handles it for `@page` pages.

---

## 11. Database access

`self.db` is the shared `DatabaseService` (SQLite). The supported
plugin-configuration API is `get_config()` / `set_config()`.

The loader creates this table:

```sql
CREATE TABLE IF NOT EXISTS plugin_config (
    id TEXT PRIMARY KEY,
    config TEXT,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
```

`DatabaseService` provides:

- `get_plugin_config(plugin_id) -> str | None`
- `save_plugin_config(plugin_id, config_json)`

There is no plugin-specific database schema or ORM. For plugin-owned data,
create your own SQLite file inside the plugin folder (as the Gemini plugin
does) or use the shared database carefully. Do not assume that the shared
schema is stable across TeleSender versions.

---

## 12. Logging and errors

Use `self.logger`:

```python
self.logger.debug("debug")
self.logger.info("info")
self.logger.warning("warning")
self.logger.error("error")
self.logger.exception("error with traceback")
```

Error handling by the runtime:

| Context | Behavior |
|---|---|
| Command handler raises | Caught by `CommandHandler`; user gets a translated error reply |
| Watcher raises | Logged; other watchers continue |
| Loop raises | Logged; the loop continues |
| Page handler raises | HTTP 500; traceback logged |
| `on_page_render` raises | HTTP 500; traceback logged |
| `get_menu_items` raises | Logged; that plugin's menu items are skipped |
| Inline handler raises | Logged; other handlers continue |
| Plugin load raises | Loader logs the exception and returns `False` |

---

## 13. Telegram client access

`self.client` is the Telethon client:

```python
me = await self.client.get_me()
await self.client.send_message(chat_id, "Hello")
```

Event handlers also receive `event`, which exposes `event.reply()`,
`event.edit()`, `event.respond()`, `event.raw_text`, `event.sender_id`,
`event.chat_id`, `event.is_private`, `event.media`, and the rest of the
Telethon `NewMessage` API.

Remember that the userbot works with the **user account**; the inline bot is a
separate client managed by `InlineManager`.

---

## 14. Inter-plugin interaction

There is no official event bus or dependency-injection API between plugins.
A plugin can access another loaded plugin through the shared service:

```python
service = getattr(self.bot, "plugin_service", None)
other = service.get_plugin_instance("other_folder") if service else None
```

`get_plugin_instance()` uses the **folder name**. `get_plugin_metadata()` uses
the manifest **name**. Treat this as an advanced, loosely coupled integration:
the other plugin may be unloaded, reloaded, or missing.

---

## 15. Current limitations (verified in source)

These are real limitations of the current runtime, not bugs in the generated
plugin:

1. `@callback` is registered but never dispatched. Use inline-form button
   callbacks instead.
2. `on_config_change()` is defined but never called automatically.
3. A class-level `config = {...}` attribute is not used by the loader. Defaults
   come from `config.json`.
4. `base.html` has no `head` block; plugin `{% block head %}` content is
   ignored.
5. Plugin pages are GET-only unless the plugin registers extra routes with
   `register_web_api_routes(app)`.
6. `register_web_api_routes()` is called only when the web server starts. A
   plugin loaded later needs a restart before its extra routes exist.
7. `get_menu_items()` does not add navigation entries by itself; pages must be
   pinned in `/plugins`.
8. `get_menu_items()` must be synchronous.
9. `@inline` handlers are called for every inline query; `inline_id` is not a
   filter.
10. `self.inline.form(..., silent=...)` accepts `silent` but does not use it.
11. `@command` metadata (`description`, `usage`, `hide`) is stored but the
    built-in `.help` command does not read it yet.
12. `register_handler()` / `register_loop()` do not dynamically register new
    handlers after loading; use decorators.
13. Plugin config is not the `config` template variable; `config` is the global
    TeleSender config.
14. Manifest `requirements` are informational only. TeleSender does not run
    `pip install` for plugins.
15. Keep the manifest `name` equal to the plugin folder name. Unload hooks,
    loop cancellation and inline handler lookup use the folder name while the
    plugin registry is keyed by the manifest name, so a mismatch can leave
    stale entries and running loop tasks behind.

---

## 16. Quick checklist for a new plugin

1. Edit `manifest.json` (`name`, `author`, `version`, `description`).
2. Edit `config.json` defaults.
3. Rename/remove the demo commands, watcher, loop and page in `plugin.py`.
4. Add translation keys to all 13 `lang/*.json` files.
5. Build your page in `templates/index.html` and assets in `static/`.
6. Load the plugin from the console or `/plugins`.
7. Pin the page in `/plugins` if it should appear in the bottom navigation.
8. Check `README.md` for the exact API contract before using an advanced
   feature.

---

## 17. Generated files

| File | Purpose |
|---|---|
| `manifest.json` | Metadata required by the loader |
| `config.json` | Default configuration |
| `plugin.py` | Plugin class with working API examples |
| `README.md` | This developer reference |
| `lang/*.json` (13 interface languages) | Plugin translations |
| `templates/index.html` | Web page template |
| `static/style.css` | Web page styles |
| `static/script.js` | Web page JavaScript |
| `__init__.py` | Package marker |
''',
        PLUGIN_NAME=name,
        PLUGIN_ID=generate_plugin_id(name, author),
        AUTHOR=author,
        DESCRIPTION=description,
        CATEGORY=category,
        COMMAND_NAME=command_name,
        CLASS_NAME=class_name,
    )


def create_plugin_scaffold(name: str, author: str, description: str,
                           category: str, create_web_page: bool = True,
                           plugins_dir: Optional[str] = None) -> Dict[str, str]:
    """Creates a complete plugin folder and returns its metadata.

    Raises:
        ValueError: invalid plugin name.
        FileExistsError: the plugin folder already exists.
    """
    name = validate_plugin_name(name)
    author = str(author or "").strip() or "User"
    description = str(description or "").strip() or "My plugin"
    category = str(category or "").strip() or "General"
    plugins_dir = plugins_dir or "plugins"

    plugin_dir = os.path.join(plugins_dir, name)
    if os.path.exists(plugin_dir):
        raise FileExistsError(name)

    class_name = plugin_class_name(name)
    command_name = plugin_command_name(name)
    manifest = build_manifest(name, author, description, category)
    config = build_config(manifest["version"])
    translations = build_translations(name)

    os.makedirs(plugin_dir, exist_ok=False)
    try:
        with open(os.path.join(plugin_dir, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)
            f.write("\n")

        with open(os.path.join(plugin_dir, "config.json"), "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
            f.write("\n")

        with open(os.path.join(plugin_dir, "__init__.py"), "w", encoding="utf-8") as f:
            f.write("# Plugin package\n")

        with open(os.path.join(plugin_dir, "plugin.py"), "w", encoding="utf-8") as f:
            f.write(build_plugin_code(name, class_name, command_name, create_web_page))

        with open(os.path.join(plugin_dir, "README.md"), "w", encoding="utf-8") as f:
            f.write(build_readme(
                name, author, description, category, command_name, class_name
            ))

        lang_dir = os.path.join(plugin_dir, "lang")
        os.makedirs(lang_dir, exist_ok=True)
        for lang_code, values in translations.items():
            with open(os.path.join(lang_dir, f"{lang_code}.json"), "w", encoding="utf-8") as f:
                json.dump(values, f, indent=2, ensure_ascii=False)
                f.write("\n")

        templates_dir = os.path.join(plugin_dir, "templates")
        static_dir = os.path.join(plugin_dir, "static")
        os.makedirs(templates_dir, exist_ok=True)
        os.makedirs(static_dir, exist_ok=True)

        with open(os.path.join(templates_dir, "index.html"), "w", encoding="utf-8") as f:
            f.write(build_template(name))

        with open(os.path.join(static_dir, "style.css"), "w", encoding="utf-8") as f:
            f.write(build_style())

        with open(os.path.join(static_dir, "script.js"), "w", encoding="utf-8") as f:
            f.write(build_script())
    except Exception:
        shutil.rmtree(plugin_dir, ignore_errors=True)
        raise

    return {
        "name": name,
        "id": manifest["id"],
        "path": plugin_dir,
        "class_name": class_name,
        "command": command_name,
        "create_web_page": create_web_page,
    }
