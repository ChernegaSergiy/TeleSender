#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Console input helper for interactive prompts and editor integrations."""

import asyncio
import os
import subprocess
import sys
import tempfile
from typing import List, Optional

from src.console.terminal import read_line
from src.services.database_service import Colors, print_color
from src.services.translator import lang


class InputHelper:
    """Helper for reading single-line or multi-line text from terminal or editor."""

    @staticmethod
    async def get_input(prompt: str) -> str:
        """Asynchronously reads a line of input from stdin."""
        loop = asyncio.get_event_loop()

        if sys.stdin.isatty() and sys.stdout.isatty():
            return await loop.run_in_executor(None, read_line, prompt)

        return await loop.run_in_executor(None, lambda: input(prompt))

    @classmethod
    async def get_multiline_simple(cls) -> str:
        """Reads multiple lines from terminal until '>>' token."""
        lines = []
        arrow_hint = lang.get("messages.enter_arrow", "(Enter >> to complete)")
        print_color(arrow_hint, Colors.BLUE)
        while True:
            line = await cls.get_input("")
            if line.strip() == ">>":
                break
            lines.append(line)
        return "\n".join(lines)

    @classmethod
    async def get_multiline_editor(cls, initial_text: str = "") -> str:
        """Opens system editor to input multi-line content."""
        help_msg = lang.get("messages.editor_help", "Enter message content (HTML supported)")
        save_msg = lang.get("messages.editor_save", "Save and exit editor")

        with tempfile.NamedTemporaryFile(mode="w+", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write(f"# {help_msg}\n")
            f.write(f"# {save_msg}\n")
            f.write("# ---------------------------------------------\n\n")
            if initial_text:
                f.write(initial_text)
            temp_file = f.name

        editor = os.environ.get("EDITOR", "nano")
        subprocess.call([editor, temp_file])

        with open(temp_file, "r", encoding="utf-8") as f:
            lines = f.readlines()

        try:
            os.unlink(temp_file)
        except Exception:
            pass

        content = [line.rstrip("\n") for line in lines if not line.startswith("#")]
        return "\n".join(content).strip()

    @classmethod
    async def get_rotation_console(cls) -> List[str]:
        """Reads multiple messages separated by '===' until '>>'."""
        messages = []
        current_msg = []

        print_color(lang.get("messages.rotation_enter", "Enter messages for rotation"), Colors.CYAN)
        print_color(lang.get("messages.rotation_hint_new", "Type === for new message"), Colors.YELLOW)
        print_color(lang.get("messages.rotation_hint_end", "Type >> to complete"), Colors.YELLOW)

        while True:
            line = await cls.get_input("")
            if line.strip() == ">>":
                if current_msg:
                    messages.append("\n".join(current_msg))
                break
            elif line.strip() == "===":
                if current_msg:
                    messages.append("\n".join(current_msg))
                    current_msg = []
                    saved_msg = lang.get("messages.rotation_saved", "Message #{num} saved").replace("{num}", str(len(messages)))
                    print_color(saved_msg, Colors.GREEN)
            else:
                current_msg.append(line)

        if current_msg:
            messages.append("\n".join(current_msg))

        return messages

    @classmethod
    async def get_rotation_editor(cls, initial_messages: Optional[List[str]] = None) -> List[str]:
        """Opens editor for composing rotation message batches separated by '==='."""
        with tempfile.NamedTemporaryFile(mode="w+", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write("# " + lang.get("messages.editor_rotation_help", "Enter messages separated by ===") + "\n\n")
            if initial_messages:
                filtered = [m for m in initial_messages if m and not m.startswith("#")]
                f.write("\n===\n".join(filtered))
            temp_file = f.name

        editor = os.environ.get("EDITOR", "nano")
        subprocess.call([editor, temp_file])

        with open(temp_file, "r", encoding="utf-8") as f:
            content = f.read()

        try:
            os.unlink(temp_file)
        except Exception:
            pass

        messages = []
        for part in content.split("==="):
            lines = [l.strip() for l in part.strip().split("\n") if l.strip() and not l.startswith("#")]
            if lines:
                messages.append("\n".join(lines).strip())

        return [m for m in messages if m]
