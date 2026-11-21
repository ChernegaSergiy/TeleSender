#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Command interface for modular bot command handlers."""

from abc import ABC, abstractmethod


class CommandInterface(ABC):
    """Abstract interface that all bot commands must implement."""

    @abstractmethod
    async def execute(self, event) -> None:
        """Executes the command in response to a Telegram event."""
        pass
