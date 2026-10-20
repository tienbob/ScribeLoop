"""Local HTTP/WS server consumed by the shell (Phase 1).

Skeleton: FastAPI app with /healthz. The WebSocket surface (live transcript,
summary updates) lands in Phase 1 with first-message auth (``auth.py``) and
loopback-only binding.
"""
from __future__ import annotations

from fastapi import FastAPI

from .auth import new_token


def create_app() -> tuple[FastAPI, str]:
    """Build the app and the per-launch token handed to the shell out-of-band."""
    app = FastAPI(title="scribeloop-core", docs_url=None, redoc_url=None)
    token = new_token()

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    return app, token


def main() -> None:  # pragma: no cover - dev entry point
    import uvicorn

    app, _token = create_app()
    uvicorn.run(app, host="127.0.0.1", port=8757, log_level="info")


if __name__ == "__main__":  # pragma: no cover
    main()
