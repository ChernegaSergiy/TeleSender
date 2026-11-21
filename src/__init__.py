#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Source root package."""

__all__ = ["Bot"]


def __getattr__(name):
    if name == "Bot":
        from src.bot import Bot
        return Bot
    raise AttributeError(name)
