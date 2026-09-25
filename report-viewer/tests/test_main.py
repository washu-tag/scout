"""Keep-alive setting: env -> Settings -> uvicorn.run."""

from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from scout_report_viewer import __main__ as entrypoint
from scout_report_viewer.config import Settings


def test_timeout_keep_alive_env_reaches_uvicorn(monkeypatch):
    monkeypatch.setenv("REPORT_VIEWER_TIMEOUT_KEEP_ALIVE", "310")
    monkeypatch.setattr(entrypoint, "settings", Settings())
    monkeypatch.setattr(entrypoint.uvicorn, "run", run := Mock())
    entrypoint.main()
    assert run.call_args.kwargs["timeout_keep_alive"] == 310


def test_timeout_keep_alive_default(monkeypatch):
    monkeypatch.delenv("REPORT_VIEWER_TIMEOUT_KEEP_ALIVE", raising=False)
    assert Settings().timeout_keep_alive == 120


def test_timeout_keep_alive_rejects_zero(monkeypatch):
    # 0 would make uvicorn close every connection right after each response.
    monkeypatch.setenv("REPORT_VIEWER_TIMEOUT_KEEP_ALIVE", "0")
    with pytest.raises(ValidationError):
        Settings()
