"""Network guard: make "100% local" something we can check, not just promise.

Python lets us register an *audit hook* that is called for sensitive runtime
events, including every ``socket.connect``. We use it to:

* allow connections to loopback addresses (127.0.0.0/8, ::1) and Unix sockets,
* record every other connection attempt, and (by default) block it by raising.

The ``/api/health`` endpoint reports the counters, which power the
"Local only" indicator in the UI.
"""

from __future__ import annotations

import ipaddress
import socket
import sys
import threading


class NetworkGuard:
    def __init__(self):
        self.enabled = False
        self.block = True
        self.blocked: list[str] = []
        self.allowed_local = 0
        self._lock = threading.Lock()

    @staticmethod
    def is_local(address) -> bool:
        if isinstance(address, (str, bytes)):  # AF_UNIX path
            return True
        host = address[0] if isinstance(address, tuple) and address else address
        if isinstance(host, bytes):
            host = host.decode()
        if host in ("localhost", ""):
            return True
        try:
            return ipaddress.ip_address(str(host).split("%")[0]).is_loopback
        except ValueError:
            return False  # a hostname that is not "localhost"

    def _hook(self, event: str, args: tuple) -> None:
        if not self.enabled or event != "socket.connect":
            return
        sock, address = args
        if getattr(sock, "family", None) == getattr(socket, "AF_UNIX", object()):
            return
        if self.is_local(address):
            with self._lock:
                self.allowed_local += 1
            return
        with self._lock:
            self.blocked.append(str(address))
        if self.block:
            raise ConnectionRefusedError(
                f"Lodestar blocked an outbound connection to {address}: only localhost is allowed."
            )

    def install(self, block: bool = True) -> None:
        """Audit hooks cannot be removed, so we install once and toggle ``enabled``."""
        self.block = block
        if not getattr(self, "_installed", False):
            sys.addaudithook(self._hook)
            self._installed = True
        self.enabled = True

    def status(self) -> dict:
        with self._lock:
            return {
                "guard_enabled": self.enabled,
                "blocking": self.block,
                "external_attempts": len(self.blocked),
                "last_blocked": self.blocked[-5:],
                "local_connections": self.allowed_local,
            }


guard = NetworkGuard()
