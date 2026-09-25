"""Entrypoint wiring: Settings values reach uvicorn.run."""

from __future__ import annotations

from typing import Any

from scout_report_viewer import __main__ as entrypoint
from scout_report_viewer.config import Settings


def _run_main(monkeypatch) -> dict[str, Any]:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        entrypoint.uvicorn, "run", lambda *args, **kwargs: calls.append(kwargs)
    )
    monkeypatch.setattr(entrypoint, "settings", Settings())
    entrypoint.main()
    assert len(calls) == 1
    return calls[0]


def test_timeout_keep_alive_env_reaches_uvicorn(monkeypatch):
    monkeypatch.setenv("REPORT_VIEWER_TIMEOUT_KEEP_ALIVE", "310")
    assert _run_main(monkeypatch).get("timeout_keep_alive") == 310


def test_timeout_keep_alive_default(monkeypatch):
    monkeypatch.delenv("REPORT_VIEWER_TIMEOUT_KEEP_ALIVE", raising=False)
    assert _run_main(monkeypatch).get("timeout_keep_alive") == 120
