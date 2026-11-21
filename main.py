#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Main entrypoint for tgscript bot."""

import os
import sys
import asyncio

# Set working directory to project root
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)

from src.bot import Bot
from src.config.config import config
from src.console.menu import Menu
from src.console.logo import show_logo
from src.services.database_service import Colors, print_color
from src.services.logger import log
from src.services.tunnel_service import TunnelService


async def run():

    show_logo()
    print()

    bot = Bot()
    tunnel = None

    try:
        # Start the tunnel before the bot so the inline bot's initial /start
        # reply already contains the public HTTPS URL.
        if config.web_enabled and config.tunnel_enabled:
            tunnel = TunnelService()
            await tunnel.start()

        await bot.start()

        if config.web_enabled:
            try:
                await bot.web_server.start()
            except Exception as e:
                log.error(f"Failed to start web interface: {e}")

        console = Menu(bot)

        if sys.stdin.isatty():
            await console.show_main_menu()
        else:
            # Started without a terminal (nohup, systemd, Docker). Reading from
            # stdin would raise EOFError and shut everything down, so just
            # keep the bot and web interface running until the process is
            # stopped externally.
            log.info("No interactive console attached; running in background")
            await asyncio.Event().wait()
    finally:
        if tunnel:
            await tunnel.stop()
        await bot.web_server.stop()


def main():
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        log.exception(f"Critical execution error: {e}")


if __name__ == "__main__":
    main()
