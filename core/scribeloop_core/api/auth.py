"""Local API auth (§6 Local API security — Phase 1 build).

- Bind 127.0.0.1 only; random per-launch bearer token handed to the shell
  out-of-band.
- Browsers cannot set Authorization headers on WebSockets → first-message
  auth: the client sends the token as its first WS frame; server closes on
  mismatch.
- Host header validated against a fixed allowlist (DNS-rebinding defense).
- Stronger alternative (preferred where available): a Unix domain socket
  owned by the shell process.
"""
from __future__ import annotations

import secrets

ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def new_token() -> str:
    """Random per-launch token — never a fixed port or secret."""
    return secrets.token_urlsafe(32)


def host_allowed(host: str) -> bool:
    """Validate the Host header against the allowlist (DNS-rebinding defense)."""
    return (host or "").split(":")[0].strip("[]").lower() in ALLOWED_HOSTS


def check_first_message(message: str | bytes, token: str) -> bool:
    """First-message auth for WebSocket clients (constant-time compare)."""
    if isinstance(message, bytes):
        message = message.decode("utf-8", errors="replace")
    return secrets.compare_digest(message.strip(), token)
