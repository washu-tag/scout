"""Unit tests for LIKE-filter escaping."""

from __future__ import annotations

from scout_report_viewer.routes.searches import _filter_clause, _like_escape


def test_like_escape_neuters_wildcards():
    assert _like_escape("50%_off!") == "50!%!_off!!"


def test_filter_clause_service_name_escapes_percent():
    sql, params = _filter_clause("service_name", ["100%"], alias="s")
    assert "ESCAPE '!'" in sql
    assert params == ["%100!%%"]
