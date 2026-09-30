"""Network guard: make "100% local" something we can check, not just promise.

Python lets us register an *audit hook* that is called for sensitive runtime
events, including every ``socket.connect``. We use it to:

* allow connections to loopback addresses (127.0.0.0/8, ::1) and Unix sockets,
* record every other connection attempt, and (by default) block it by raising.

The ``/api/health`` endpoint reports the counters, which power the
"Local only" indicator in the UI.
"""

from __future__ import annotations

import contextlib
import ipaddress
import socket
import sys
import threading
import time


class NetworkGuard:
    def __init__(self):
        self.enabled = False
        self.block = True
        self.blocked: list[str] = []
        self.allowed_local = 0
        self.allowed_remote = 0
        self._installed = False
        self._allow_names: set[str] = set()
        self._allow_nets: list = []
        self._resolved: set[str] = set()
        self._resolved_at = 0.0
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

    def allow(self, hosts: list[str]) -> None:
        """Let connections to these hosts through (IPs, CIDR ranges or host names).

        Host names are resolved to addresses, because the audit hook usually sees the address a
        name resolved to. Everything not listed stays blocked.
        """
        self._allow_names, self._allow_nets = set(), []
        for host in hosts:
            try:
                self._allow_nets.append(ipaddress.ip_network(host, strict=False))
            except ValueError:
                self._allow_names.add(host.lower())
        self._resolved, self._resolved_at = set(), 0.0
        self._resolve()

    def _resolve(self) -> None:
        found: set[str] = set()
        for name in self._allow_names:
            with contextlib.suppress(OSError):
                found.update(info[4][0] for info in socket.getaddrinfo(name, None))
        self._resolved, self._resolved_at = found, time.monotonic()

    def is_allowed(self, address) -> bool:
        """True when ``address`` belongs to an explicitly allowed remote host."""
        if not (self._allow_names or self._allow_nets):
            return False
        host = address[0] if isinstance(address, tuple) and address else address
        if isinstance(host, bytes):
            host = host.decode()
        host = str(host).split("%")[0]
        if host.lower() in self._allow_names:
            return True
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            return False
        if any(ip in net for net in self._allow_nets):
            return True
        if str(ip) not in self._resolved and time.monotonic() - self._resolved_at > 30:
            self._resolve()  # DNS answers change: look again, at most every 30 seconds
        return str(ip) in {str(ipaddress.ip_address(r)) for r in self._resolved}

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
        if self.is_allowed(address):
            with self._lock:
                self.allowed_remote += 1
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
        if not self._installed:
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
                "remote_connections": self.allowed_remote,
                "allowed_hosts": sorted(self._allow_names | {str(n) for n in self._allow_nets}),
            }


guard = NetworkGuard()
