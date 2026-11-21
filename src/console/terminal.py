#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Console prompt renderer with a reserved timer panel below the input line.

The prompt is drawn by this module instead of ``input()`` so the timer panel can
be refreshed without ever moving the user's text away from the ``Choice:`` line.
Background notifications (for example completed sends) are printed above the
prompt, and the prompt plus its panel are redrawn underneath them.
Only basic ANSI sequences are used (cursor up / left), which keeps the layout
stable even on terminals that do not support cursor save/restore.
"""

import codecs
import os
import select
import shutil
import signal
import sys
import termios
import threading
import time
import unicodedata
from typing import Dict, List, Optional, Tuple

PANEL_HEIGHT = 3

_lock = threading.RLock()
_statuses: Dict[str, str] = {}
_prompt_active = False
_prompt_text = ""
_prompt_buffer = ""
_prompt_cursor = 0
_last_size: Optional[Tuple[int, int]] = None
_redraw_needed = threading.Event()


# ---------------------------------------------------------------------------
# Terminal resize / resume handling
# ---------------------------------------------------------------------------

def _redraw_on_signal(signum, frame) -> None:  # noqa: ARG001
    """Marks the prompt for a redraw after resize or process resume."""
    _redraw_needed.set()


def _install_signal_handlers() -> None:
    """Requests a panel refresh when the terminal is resized or resumed.

    Redrawing is deferred to the prompt reader: signal handlers only set a
    flag, so they never write to the terminal from an arbitrary context.
    """
    for name in ("SIGWINCH", "SIGCONT"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, _redraw_on_signal)
        except (OSError, ValueError):
            # Signals can only be installed from the main thread. When this
            # module is imported elsewhere, size polling still handles resizes.
            pass


_install_signal_handlers()


# ---------------------------------------------------------------------------
# Status panel
# ---------------------------------------------------------------------------

def update_status(key: str, message: str) -> None:
    """Adds or replaces a background job status in the panel."""
    with _lock:
        _statuses[key] = message
        if _prompt_active:
            _render_prompt_and_panel()


def clear_status(key: str) -> None:
    """Removes a background job status from the panel."""
    with _lock:
        _statuses.pop(key, None)
        if _prompt_active:
            _render_prompt_and_panel()


def write_line(message: str = "") -> None:
    """Prints a background message above an active prompt instead of dropping it."""
    with _lock:
        if _prompt_active and _is_tty():
            _write_above_prompt_locked(message)
            return
        print(message)


def _is_tty() -> bool:
    return sys.stdout.isatty()


def _terminal_size() -> Tuple[int, int]:
    """Returns (columns, rows) for the real terminal, not stale env vars.

    ``shutil.get_terminal_size`` prefers ``$COLUMNS`` / ``$LINES`` when they are
    exported. Termux keeps those values from the first launch, so a resize or a
    keyboard show/hide would never be noticed. Querying the fd directly is the
    only reliable way to detect the new size.
    """
    try:
        size = os.get_terminal_size(sys.stdout.fileno())
    except (OSError, ValueError, AttributeError):
        size = shutil.get_terminal_size((80, 24))
    return (max(1, size.columns), max(1, size.lines))


def _panel_width() -> int:
    return _terminal_size()[0]


def _display_width(text: str) -> int:
    """Returns the number of terminal columns occupied by ``text``."""
    width = 0
    for char in text:
        if unicodedata.combining(char):
            continue
        width += 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
    return width


def _panel_lines() -> List[str]:
    width = max(1, _panel_width() - 1)
    text = " | ".join(_statuses.values())
    if text:
        text = "  " + text
    if _display_width(text) > width:
        truncated = ""
        for char in text:
            if _display_width(truncated + char) > width - 1:
                break
            truncated += char
        text = truncated + "…"
    separator = "─" * width
    return [separator, text, separator]


def _draw_prompt_locked() -> None:
    sys.stdout.write("\r\033[2K" + _prompt_text + _prompt_buffer)
    back = _display_width(_prompt_buffer[_prompt_cursor:])
    if back > 0:
        sys.stdout.write(f"\033[{back}D")
    sys.stdout.flush()


def _render_prompt_and_panel(clear_below: bool = False) -> None:
    """Redraws the status panel below the prompt and restores the prompt line.

    ``clear_below`` wipes everything after the cursor first. This repairs the
    panel after the terminal reflows it (resize, keyboard show/hide, or when
    Termux is resumed) and the old bottom separator line no longer matches.
    """
    global _last_size
    if not _is_tty():
        return

    size = _terminal_size()
    if _last_size is not None and size != _last_size:
        # The terminal reflowed since the last paint (resize, keyboard show or
        # hide, Termux resume). Repaint from a clean slate so a stale separator
        # of the previous width cannot survive below the new one.
        clear_below = True
    _last_size = size

    if clear_below:
        sys.stdout.write("\033[J")
    for line in _panel_lines():
        sys.stdout.write("\n\r\033[2K" + line)
    sys.stdout.write(f"\033[{PANEL_HEIGHT}A")
    _draw_prompt_locked()


def _refresh_prompt_if_needed() -> None:
    """Redraws the prompt when the terminal was resized or the app resumed."""
    with _lock:
        if not _prompt_active or not _is_tty():
            _redraw_needed.clear()
            return

        resized = _terminal_size() != _last_size
        if resized or _redraw_needed.is_set():
            _redraw_needed.clear()
            _render_prompt_and_panel(clear_below=True)


def _clear_panel_locked() -> None:
    """Clears the panel lines and leaves the cursor on the first freed line."""
    for index in range(PANEL_HEIGHT):
        sys.stdout.write("\r\033[2K")
        if index < PANEL_HEIGHT - 1:
            sys.stdout.write("\n")
    sys.stdout.write(f"\033[{PANEL_HEIGHT - 1}A\r")
    sys.stdout.flush()


def _write_above_prompt_locked(message: str) -> None:
    """Prints a message on the prompt line and moves the prompt one line down.

    The cursor is expected to be on the prompt line. The panel below it is
    erased, the message is written in its place, and then the prompt and the
    panel are redrawn one line lower. This keeps notifications above the
    ``Choice:`` line without relying on cursor save/restore support.
    """
    # Erase the panel but keep the cursor on the prompt line.
    sys.stdout.write("\n")
    for _ in range(PANEL_HEIGHT):
        sys.stdout.write("\r\033[2K\n")
    sys.stdout.write(f"\033[{PANEL_HEIGHT + 1}A")

    # Replace the prompt line with the notification.
    sys.stdout.write("\r\033[2K" + message + "\n")

    # Draw the prompt and its panel directly below the notification.
    _render_prompt_and_panel()


# ---------------------------------------------------------------------------
# Prompt lifecycle
# ---------------------------------------------------------------------------

def _begin_prompt(prompt: str) -> None:
    global _prompt_active, _prompt_text, _prompt_buffer, _prompt_cursor
    with _lock:
        _prompt_active = True
        _prompt_buffer = ""
        _prompt_cursor = 0
        _redraw_needed.clear()

        # A prompt may start with one or more newlines (for example
        # "\nChoice: "). Print those first so that the prompt line and the
        # panel below it always start from a clean, known position.
        head, separator, prompt_line = prompt.rpartition("\n")
        if separator:
            sys.stdout.write(head + separator)
        _prompt_text = prompt_line

        _render_prompt_and_panel()


def _set_prompt_buffer(buffer: str, cursor: int) -> None:
    global _prompt_buffer, _prompt_cursor
    with _lock:
        _prompt_buffer = buffer
        _prompt_cursor = cursor
        _draw_prompt_locked()


def _finish_prompt() -> None:
    global _prompt_active
    with _lock:
        sys.stdout.write("\r\033[2K" + _prompt_text + _prompt_buffer + "\n")
        _clear_panel_locked()
        _prompt_active = False


def _abort_prompt(marker: str = "^C") -> None:
    global _prompt_active
    with _lock:
        sys.stdout.write("\r\033[2K" + _prompt_text + _prompt_buffer + marker + "\n")
        _clear_panel_locked()
        _prompt_active = False


# ---------------------------------------------------------------------------
# Line reader
# ---------------------------------------------------------------------------

def _read_escape(fd: int) -> Optional[str]:
    """Reads an ANSI escape sequence after ESC; returns its final part."""
    if not select.select([fd], [], [], 0.05)[0]:
        return None

    first = os.read(fd, 1)
    if first not in (b"[", b"O"):
        return None

    sequence = bytearray(first)
    while True:
        if not select.select([fd], [], [], 0.05)[0]:
            break
        chunk = os.read(fd, 1)
        if not chunk:
            break
        sequence += chunk
        if chunk.isalpha() or chunk == b"~":
            break
    return sequence.decode("ascii", "ignore")


def _apply_escape(sequence: Optional[str], buffer: List[str], cursor: int) -> int:
    if not sequence:
        return cursor
    if sequence in ("[D", "OD"):
        return max(0, cursor - 1)
    if sequence in ("[C", "OC"):
        return min(len(buffer), cursor + 1)
    if sequence in ("[H", "OH", "[1~", "[7~"):
        return 0
    if sequence in ("[F", "OF", "[4~", "[8~"):
        return len(buffer)
    if sequence == "[3~" and cursor < len(buffer):
        del buffer[cursor]
    return cursor


def read_line(prompt: str) -> str:
    """Reads one line with a live status panel below it."""
    global _prompt_active
    fd = sys.stdin.fileno()
    original = termios.tcgetattr(fd)
    settings = termios.tcgetattr(fd)
    settings[3] &= ~(termios.ECHO | termios.ICANON | termios.IEXTEN | termios.ISIG)
    settings[6][termios.VMIN] = 1
    settings[6][termios.VTIME] = 0

    buffer: List[str] = []
    cursor = 0
    decoder = codecs.getincrementaldecoder("utf-8")("replace")

    termios.tcsetattr(fd, termios.TCSANOW, settings)
    try:
        _begin_prompt(prompt)
        last_tick = time.time()
        while True:
            try:
                ready = select.select([fd], [], [], 0.25)[0]
            except InterruptedError:
                ready = []

            now = time.time()
            if now - last_tick > 1.5:
                # The process was most likely suspended while the terminal
                # (Termux) was in the background; repair the panel on resume.
                _redraw_needed.set()
            last_tick = now

            # Repairs the panel after a resize, a keyboard show/hide or a
            # Termux resume, even if a key is already waiting to be read.
            _refresh_prompt_if_needed()

            if not ready:
                continue

            try:
                data = os.read(fd, 1)
            except InterruptedError:
                continue
            if not data:
                _abort_prompt("")
                raise EOFError

            char = decoder.decode(data)
            if not char:
                continue

            if char in ("\r", "\n"):
                _finish_prompt()
                return "".join(buffer)

            if char in ("\x7f", "\b"):
                if cursor > 0:
                    del buffer[cursor - 1]
                    cursor -= 1
                    _set_prompt_buffer("".join(buffer), cursor)
                continue

            if char == "\x03":
                _abort_prompt("^C")
                raise KeyboardInterrupt

            if char == "\x04":
                if not buffer:
                    _abort_prompt("^D")
                    raise EOFError
                continue

            if char == "\x1b":
                new_cursor = _apply_escape(_read_escape(fd), buffer, cursor)
                if new_cursor != cursor or buffer:
                    cursor = new_cursor
                    _set_prompt_buffer("".join(buffer), cursor)
                continue

            if char < " ":
                continue

            buffer.insert(cursor, char)
            cursor += 1
            _set_prompt_buffer("".join(buffer), cursor)
    finally:
        with _lock:
            if _prompt_active:
                try:
                    _abort_prompt("")
                except Exception:
                    _prompt_active = False
        termios.tcsetattr(fd, termios.TCSANOW, original)
