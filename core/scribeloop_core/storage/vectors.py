"""Vector index for session retrieval (Flow 4).

sqlite-vec behind a ``VectorIndex`` interface (§10): version PINNED, swappable
for Qdrant if the index outgrows ~10^7 chunks. Phase 0a checks:
- SQLCipher + sqlite-vec coexistence (build-flag dependent, §6)
- whether deleting from a ``vec0`` table scrubs vector bytes (§3.1) — if not,
  chunks are rebuilt into a fresh table on purge.
"""
from __future__ import annotations

from typing import Protocol


class VectorIndex(Protocol):
    def add(self, session_id: str, chunk_start_ms: int, chunk_end_ms: int,
            segment_ids: list[str], chunk_text: str, vector: bytes) -> None: ...

    def search(self, vector: bytes, k: int = 8) -> list[dict]: ...

    def delete_session(self, session_id: str) -> None: ...


class SqliteVecIndex:
    """sqlite-vec vec0 implementation — Phase 0a build + checks above."""

    def __init__(self) -> None:
        raise NotImplementedError("Phase 0a — see module docstring and §6/§3.1 checks")
