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
from urllib.parse import urlsplit

ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def new_token() -> str:
    """Random per-launch token — never a fixed port or secret."""
    return secrets.token_urlsafe(32)


def host_allowed(host: str) -> bool:
    """Validate the Host header against the allowlist (DNS-rebinding defense)."""
    if not host or any(ord(char) <= 32 or ord(char) >= 127 for char in host):
        return False
    try:
        parsed = urlsplit('//' + host)
        port = parsed.port
        return (parsed.hostname in ALLOWED_HOSTS and parsed.username is None
                and parsed.password is None and not parsed.path and not parsed.query
                and not parsed.fragment and (port is None or 0 < port <= 65535))
    except ValueError:
        return False


def check_first_message(message: str | bytes, token: str) -> bool:
    """First-message auth for WebSocket clients (constant-time compare)."""
    if isinstance(message, bytes):
        message = message.decode("utf-8", errors="replace")
    try:
        return secrets.compare_digest(message.strip().encode('utf-8'), token.encode('utf-8'))
    except UnicodeError:
        return False


def origin_allowed(origin: str | None, host: str) -> bool:
    """No Origin is permitted for token-authenticated native clients; browser
    origins must be well-formed and match the serving authority exactly.
    """
    if origin is None:
        return True
    if any(ord(char) <= 32 or ord(char) >= 127 for char in origin):
        return False
    try:
        parsed = urlsplit(origin)
        return (parsed.scheme in {'http', 'https'} and parsed.netloc == host
                and host_allowed(parsed.netloc) and not parsed.path
                and not parsed.query and not parsed.fragment)
    except ValueError:
        return False
