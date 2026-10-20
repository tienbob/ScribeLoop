"""MCP tool surface (Phase 3 — ARCHITECTURE.md §5).

Third-party MCP servers are version-pinned; their network egress reviewed
before enabling (§5 exfiltration channels).
"""
from __future__ import annotations


class MCPToolbox:
    def __init__(self) -> None:
        raise NotImplementedError("Phase 3 — see ARCHITECTURE.md §5")
