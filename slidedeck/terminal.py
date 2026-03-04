"""PTY + tmux terminal manager for browser-based terminal access."""

from __future__ import annotations

import asyncio
import fcntl
import json
import logging
import os
import pty
import struct
import termios

import aiohttp.web as web

logger = logging.getLogger("slidedeck.terminal")


class TerminalManager:
    """Attaches to a tmux session via PTY and relays I/O to WebSocket clients."""

    def __init__(self, session: str = "claude") -> None:
        self.session = session
        self._pid: int | None = None
        self._fd: int | None = None
        self._read_task: asyncio.Task | None = None
        self._clients: set[web.WebSocketResponse] = set()
        self._running = False

    async def start(self) -> None:
        """Lazy start — called on first WebSocket connect."""
        if self._running:
            return

        # Check tmux is installed and session exists
        try:
            proc = await asyncio.create_subprocess_exec(
                "tmux", "has-session", "-t", self.session,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            rc = await proc.wait()
        except FileNotFoundError:
            raise RuntimeError("tmux is not installed")

        if rc != 0:
            raise RuntimeError(f"tmux session '{self.session}' not found. Start it with: tmux new -s {self.session}")

        # Fork a PTY and attach to the tmux session
        pid, fd = pty.fork()
        if pid == 0:
            # Child — exec into tmux attach
            os.execvp("tmux", ["tmux", "attach-session", "-t", self.session])
            # Never reached
        else:
            # Parent
            self._pid = pid
            self._fd = fd
            self._running = True

            # Set fd non-blocking
            flags = fcntl.fcntl(fd, fcntl.F_GETFL)
            fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

            # Set initial size (80x24)
            self.resize(80, 24)

            # Start read loop
            self._read_task = asyncio.create_task(self._read_loop())
            logger.info("Terminal attached to tmux session '%s' (pid=%d)", self.session, pid)

    async def _read_loop(self) -> None:
        """Read PTY output and broadcast to all WS clients."""
        loop = asyncio.get_running_loop()
        fd = self._fd
        assert fd is not None

        try:
            while self._running:
                # Wait for fd to be readable
                event = asyncio.Event()
                loop.add_reader(fd, event.set)
                try:
                    await event.wait()
                finally:
                    loop.remove_reader(fd)

                try:
                    data = os.read(fd, 65536)
                    if not data:
                        break
                except OSError:
                    break

                # Broadcast to all clients
                dead: set[web.WebSocketResponse] = set()
                for client in self._clients:
                    try:
                        await client.send_bytes(data)
                    except (ConnectionResetError, ConnectionError):
                        dead.add(client)
                self._clients.difference_update(dead)
        except asyncio.CancelledError:
            return
        finally:
            self._running = False
            # Notify remaining clients
            msg = json.dumps({"type": "session_ended", "message": "tmux session ended"})
            for client in list(self._clients):
                try:
                    await client.send_str(msg)
                except Exception:
                    pass
            logger.info("Terminal read loop ended for session '%s'", self.session)

    def write(self, data: bytes) -> None:
        """Send browser keystrokes to PTY."""
        if self._fd is not None and self._running:
            os.write(self._fd, data)

    def resize(self, cols: int, rows: int) -> None:
        """Set terminal size via ioctl."""
        if self._fd is not None:
            winsize = struct.pack("HHHH", rows, cols, 0, 0)
            fcntl.ioctl(self._fd, termios.TIOCSWINSZ, winsize)

    def add_client(self, ws: web.WebSocketResponse) -> None:
        self._clients.add(ws)
        logger.info("Terminal client connected (%d total)", len(self._clients))

    def remove_client(self, ws: web.WebSocketResponse) -> None:
        self._clients.discard(ws)
        logger.info("Terminal client disconnected (%d total)", len(self._clients))

    async def stop(self) -> None:
        """Clean up PTY and child process."""
        self._running = False

        if self._read_task and not self._read_task.done():
            self._read_task.cancel()
            try:
                await self._read_task
            except asyncio.CancelledError:
                pass

        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None

        if self._pid is not None:
            try:
                os.waitpid(self._pid, os.WNOHANG)
            except ChildProcessError:
                pass
            self._pid = None

        logger.info("Terminal manager stopped")
