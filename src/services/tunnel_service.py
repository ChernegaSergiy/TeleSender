#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Automatic HTTPS tunnel for the web interface using Cloudflare quick tunnels.

The service starts cloudflared, waits for it to report the assigned HTTPS URL,
and writes that URL to config.web_public_url.

The tunnel is deliberately left running when the bot exits. A quick tunnel
gets a new random hostname every time it starts, and browser cookies are bound
to the hostname, so restarting the tunnel would log every browser session out.
Instead the process and its public URL are recorded in
``data/tunnel_state.json``; the next bot start reuses the still-running tunnel
and keeps the same URL.

If the tunnel process dies, it is restarted automatically (with a new URL).
"""

import asyncio
import json
import os
import re
import shutil
import signal
import time
from pathlib import Path
from typing import Optional

from src.config.config import config
from src.services.logger import log


class TunnelService:
    """Runs a long-lived Cloudflare quick tunnel and tracks its public URL."""

    _URL_RE = re.compile(
        r"https://(?!api\.)[a-zA-Z0-9-]+\.trycloudflare\.com"
    )

    _READY_MARKERS = (
        "trycloudflare.com",
    )

    _ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

    _RECONNECT_DELAY = 5.0
    _WATCH_INTERVAL = 5.0

    _STATE_PATH = Path(__file__).resolve().parents[2] / "data" / "tunnel_state.json"

    # cloudflared must not write to a pipe owned by the bot: when the bot
    # exits the pipe is closed and the next write kills the tunnel with
    # SIGPIPE, which would rotate the hostname and log browsers out. A plain
    # log file keeps the process completely independent of its parent.
    _LOG_PATH = Path(__file__).resolve().parents[2] / "data" / "cloudflared.log"

    def __init__(
        self,
        local_port: Optional[int] = None,
    ):
        self.local_port = local_port or config.web_port
        self.provider = "cloudflared"

        self.url: Optional[str] = None
        self._process: Optional[asyncio.subprocess.Process] = None
        self._reader_task: Optional[asyncio.Task] = None
        self._supervisor_task: Optional[asyncio.Task] = None
        self._watch_task: Optional[asyncio.Task] = None
        self._reused_pid: Optional[int] = None

        self._ready = asyncio.Event()
        self._connected = False
        self._stopping = False

    @property
    def running(self) -> bool:
        """Whether a tunnel process is currently alive."""
        if self._process is not None and self._process.returncode is None:
            return True
        return bool(self._reused_pid and self._pid_alive(self._reused_pid))

    async def start(self) -> bool:
        """Start (or reuse) the tunnel and wait until its URL is known."""
        if self.running:
            return True

        self._stopping = False
        self.provider = "cloudflared"

        provider = (config.tunnel_provider or "auto").lower()
        if provider not in ("auto", "cloudflared"):
            log.warning(
                f"Tunnel provider '{provider}' is not supported; "
                "only cloudflared is implemented"
            )
            return False

        # Prefer the tunnel that survived the previous bot run: its hostname
        # is already known to the browser, so cookies stay valid.
        if self._reuse_existing():
            return True

        log.info("Starting Cloudflare quick tunnel")

        if await self._start_current():
            if (
                self._supervisor_task is None
                or self._supervisor_task.done()
            ):
                self._supervisor_task = asyncio.create_task(
                    self._supervise()
                )

            return True

        await self._stop_process()

        log.warning(
            "Cloudflare tunnel could not be started; "
            "using the fallback web URL"
        )

        return False

    async def _start_current(self) -> bool:
        """Start cloudflared and wait for its public URL."""
        if not await self._spawn():
            return False

        try:
            await asyncio.wait_for(
                self._ready.wait(),
                timeout=config.tunnel_start_timeout,
            )
        except asyncio.TimeoutError:
            log.warning(
                "Cloudflare tunnel did not report a public URL within "
                f"{config.tunnel_start_timeout:.0f} s"
            )

        return self._connected and bool(self.url)

    def _command(self) -> Optional[list]:
        """Build the cloudflared command."""
        cloudflared = shutil.which("cloudflared")

        if not cloudflared:
            return None

        return [
            cloudflared,
            "tunnel",
            "--no-autoupdate",
            "--protocol",
            "http2",
            "--edge-ip-version",
            "4",
            "--url",
            f"http://127.0.0.1:{self.local_port}",
        ]

    async def _spawn(self) -> bool:
        """Create the tunnel process and its output tail."""
        command = self._command()

        if not command:
            log.warning(
                "Cloudflare tunnel disabled: cloudflared executable not found"
            )
            return False

        try:
            self._LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            log_file = open(self._LOG_PATH, "ab", buffering=0)
        except OSError as e:
            log.error(f"Failed to open tunnel log {self._LOG_PATH}: {e}")
            return False

        try:
            start_offset = self._LOG_PATH.stat().st_size
        except OSError:
            start_offset = 0

        try:
            self._process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=log_file,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )

        except Exception as e:
            log.error(f"Failed to start Cloudflare tunnel: {e}")
            return False

        finally:
            log_file.close()

        log.info(f"Tunnel command: {' '.join(command)}")

        self._reused_pid = None
        self._connected = False
        self._ready.clear()

        self._reader_task = asyncio.create_task(
            self._tail_output(self._process, start_offset)
        )

        return True

    def _apply_url(self, url: str) -> None:
        """Publish the confirmed tunnel URL."""
        if self._connected and self.url == url:
            return

        self._connected = True
        self.url = url

        config.web_public_url = url

        log.info(
            f"Web interface public URL: {url} "
            f"(config.web_public_url={config.web_public_url})"
        )

        self._save_state()
        self._ready.set()

    def _handle_output_line(self, raw: bytes) -> None:
        """Log one cloudflared output line and capture the public URL."""
        line = self._ANSI_RE.sub(
            "",
            raw.decode("utf-8", "replace"),
        ).strip()

        if not line:
            return

        log.info(f"tunnel: {line[:300]}")

        if not any(
            marker in line
            for marker in self._READY_MARKERS
        ):
            return

        match = self._URL_RE.search(line)

        if match:
            url = match.group(0).rstrip(".,)|")

            log.info(f"Tunnel URL matched: {url} (line: {line[:200]})")

            if url != self.url:
                self._apply_url(url)

    async def _tail_output(
        self,
        process: asyncio.subprocess.Process,
        start_offset: int,
    ) -> None:
        """Tail the tunnel log file and capture its public HTTPS URL."""
        position = max(0, int(start_offset))
        buffer = b""

        try:
            while True:
                chunk = b""

                try:
                    size = self._LOG_PATH.stat().st_size
                except OSError:
                    size = position

                if size < position:
                    # The log was truncated or rotated: start from the top.
                    position = 0

                if size > position:
                    try:
                        with self._LOG_PATH.open("rb") as handle:
                            handle.seek(position)
                            chunk = handle.read(size - position)
                    except OSError as e:
                        log.debug(f"Tunnel log read failed: {e}")
                        chunk = b""

                    position += len(chunk)
                    buffer += chunk

                    while b"\n" in buffer:
                        raw, buffer = buffer.split(b"\n", 1)
                        self._handle_output_line(raw)

                exited = process.returncode is not None

                if exited and not chunk:
                    break

                if not exited:
                    await asyncio.sleep(0.2)

        except asyncio.CancelledError:
            raise

        except Exception as e:
            log.debug(f"Tunnel output tail stopped: {e}")

        finally:
            self._ready.set()

    async def _supervise(self) -> None:
        """Restart cloudflared whenever the owned tunnel process exits."""
        while not self._stopping:
            process = self._process

            if process is None:
                return

            try:
                await process.wait()

            except asyncio.CancelledError:
                raise

            if self._stopping:
                return

            log.warning(
                "Cloudflare tunnel disconnected "
                f"(code {process.returncode}); "
                f"reconnecting in {self._RECONNECT_DELAY:.0f} s"
            )

            self._connected = False
            self._ready.clear()
            self._clear_state()

            await asyncio.sleep(self._RECONNECT_DELAY)

            if self._stopping:
                return

            if not await self._spawn():
                await asyncio.sleep(self._RECONNECT_DELAY)

    async def _watch_reused(self, pid: int) -> None:
        """Watch a tunnel that was inherited from the previous bot run."""
        try:
            while not self._stopping:
                await asyncio.sleep(self._WATCH_INTERVAL)
                if self._pid_alive(pid):
                    continue

                log.warning(
                    "Reused Cloudflare tunnel exited; starting a new one"
                )
                break

            if self._stopping:
                return

            self._reused_pid = None
            self._connected = False
            self.url = None
            self._clear_state()

            if await self._start_current():
                if (
                    self._supervisor_task is None
                    or self._supervisor_task.done()
                ):
                    self._supervisor_task = asyncio.create_task(
                        self._supervise()
                    )

        except asyncio.CancelledError:
            raise

    def _reuse_existing(self) -> bool:
        """Reuse the tunnel left running by the previous bot process."""
        state = self._load_state()

        if not state:
            return False

        provider = state.get("provider")
        if provider and provider != self.provider:
            self._clear_state()
            return False

        try:
            pid = int(state.get("pid"))
            url = str(state.get("url") or "")
        except (TypeError, ValueError):
            self._clear_state()
            return False

        if not pid or not url or not self._pid_is_cloudflared(pid):
            self._clear_state()
            return False

        self._reused_pid = pid
        self.url = url
        self._connected = True
        config.web_public_url = url

        log.info(
            f"Reusing existing Cloudflare tunnel (pid {pid}): {url}"
        )

        if self._watch_task is None or self._watch_task.done():
            self._watch_task = asyncio.create_task(
                self._watch_reused(pid)
            )

        return True

    def _load_state(self) -> Optional[dict]:
        try:
            raw = json.loads(self._STATE_PATH.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as e:
            log.debug(f"Could not read tunnel state: {e}")
            return None

        return raw if isinstance(raw, dict) else None

    def _save_state(self) -> None:
        pid = self._process.pid if self._process is not None else self._reused_pid

        if not pid or not self.url:
            return

        payload = {
            "pid": int(pid),
            "url": self.url,
            "provider": self.provider,
            "updated_at": time.time(),
        }

        try:
            self._STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self._STATE_PATH.with_suffix(".tmp")
            tmp_path.write_text(json.dumps(payload), encoding="utf-8")
            os.replace(tmp_path, self._STATE_PATH)
        except OSError as e:
            log.debug(f"Could not persist tunnel state: {e}")

    def _clear_state(self) -> None:
        try:
            self._STATE_PATH.unlink()
        except FileNotFoundError:
            pass
        except OSError as e:
            log.debug(f"Could not remove tunnel state: {e}")

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False
        return True

    @staticmethod
    def _pid_is_cloudflared(pid: int) -> bool:
        try:
            cmdline = (
                Path(f"/proc/{pid}/cmdline")
                .read_bytes()
                .replace(b"\0", b" ")
                .decode("utf-8", "replace")
            )
        except OSError:
            return False

        return "cloudflared" in cmdline

    async def _stop_process(self) -> None:
        """Stop the current tunnel process and output reader."""
        if self._reader_task:
            self._reader_task.cancel()

            try:
                await self._reader_task

            except (asyncio.CancelledError, Exception):
                pass

            self._reader_task = None

        process = self._process
        self._process = None

        if process and process.returncode is None:
            try:
                os.killpg(
                    os.getpgid(process.pid),
                    signal.SIGTERM,
                )

            except (ProcessLookupError, PermissionError):
                process.terminate()

            try:
                await asyncio.wait_for(
                    process.wait(),
                    timeout=5,
                )

            except asyncio.TimeoutError:
                try:
                    os.killpg(
                        os.getpgid(process.pid),
                        signal.SIGKILL,
                    )

                except (ProcessLookupError, PermissionError):
                    process.kill()

                await process.wait()

        pid = self._reused_pid
        self._reused_pid = None

        if pid and self._pid_alive(pid):
            try:
                os.kill(pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass

    async def stop(self, keep_alive: Optional[bool] = None) -> None:
        """Stop the tunnel, or leave it running for the next bot start.

        Leaving it running keeps the public hostname (and therefore browser
        sessions) stable across bot restarts.
        """
        if keep_alive is None:
            keep_alive = getattr(config, "tunnel_keep_alive", True)

        self._stopping = True

        for task_name in ("_supervisor_task", "_watch_task"):
            task = getattr(self, task_name)
            if task:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
                setattr(self, task_name, None)

        pid = self._process.pid if self._process is not None else self._reused_pid

        if keep_alive and self._connected and self.url and pid and self._pid_alive(pid):
            self._save_state()

            if self._reader_task:
                self._reader_task.cancel()
                try:
                    await self._reader_task
                except (asyncio.CancelledError, Exception):
                    pass
                self._reader_task = None

            self._reused_pid = int(pid)
            self._process = None

            log.info(
                f"Tunnel left running for the next start: {self.url}"
            )
            return

        await self._stop_process()
        self._clear_state()

        log.info("Cloudflare tunnel stopped")
