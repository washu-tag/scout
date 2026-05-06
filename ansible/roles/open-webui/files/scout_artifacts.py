"""
title: Scout Artifacts
description: Result-id artifact protocol shared by Scout's search_reports,
             read_reports, load_id_list, and the Send-to-XNAT Action. Implements
             the recipe + rows shape from scout-tool-design.md §5: a small JSON
             blob (recipe + identifier columns + included/why_excluded flags)
             persisted to OWUI Files. Row data and report text stay in Trino
             and are re-derived at use time. No new datastore.
author: Scout Team
version: 0.1.0
"""

from __future__ import annotations

import inspect
import io
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

log = logging.getLogger(__name__)

# Tag used to distinguish Scout artifacts from arbitrary user uploads. The XNAT
# Action and any in-cohort search filter on this when listing files.
SCOUT_RESULT_META_KEY = "scout_result"

# Threshold for switching from `IN (...)` literal to `IN (SELECT id FROM
# (VALUES ...))` shape — friendlier to Trino's planner at large cardinalities.
VALUES_SHAPE_THRESHOLD = 5000

# Most-specific identifier wins when picking the substitution column.
SUBSTITUTION_COLUMN_PREFERENCE = (
    "message_control_id",
    "accession_number",
    "epic_mrn",
)

COHORT_PLACEHOLDER = "{{cohort}}"


# ---------------------------------------------------------------------------
# Async-aware helpers — OWUI Files / Storage APIs are sync in some versions
# and async coroutines in others (changed during the 14.x line). All public
# helpers here `await` results that are awaitable so callers don't have to.
# ---------------------------------------------------------------------------


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------


async def write_search_artifact(
    recipe: dict,
    rows: list[dict],
    user_id: str,
    chat_id: str,
) -> str:
    """Persist a search-derived artifact (recipe + rows with included/why_excluded
    flags). Returns the OWUI file id (the result_id). No-op (returns "") if
    user_id is missing — the LLM context still gets the search result, just no
    persistence. Persistence is column-presence triggered: the caller decides
    whether to call this based on whether identifier columns are in `rows`."""
    payload = {
        "recipe": recipe,
        "rows": rows,
    }
    return await _write_artifact(payload, user_id=user_id, chat_id=chat_id)


async def write_id_list_artifact(
    recipe: dict,
    rows: list[dict],
    unmatched: list,
    user_id: str,
    chat_id: str,
) -> str:
    """Persist an upload-derived artifact (recipe + identity rows + unmatched
    triage list). Returns the OWUI file id."""
    payload = {
        "recipe": recipe,
        "rows": rows,
        "unmatched": unmatched,
    }
    return await _write_artifact(payload, user_id=user_id, chat_id=chat_id)


async def _write_artifact(payload: dict, user_id: str, chat_id: str) -> str:
    if not user_id:
        log.warning("scout_artifacts: no user_id; skipping write")
        return ""

    from open_webui.models.files import Files, FileForm
    from open_webui.storage.provider import Storage

    file_id = str(uuid.uuid4())
    filename = f"scout_result_{file_id}.json"
    payload = {
        "file_id": file_id,
        "chat_id": chat_id,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        **payload,
    }
    body = json.dumps(payload, default=str).encode("utf-8")

    _contents, file_path = await _maybe_await(
        Storage.upload_file(io.BytesIO(body), filename, {})
    )
    await _maybe_await(
        Files.insert_new_file(
            user_id,
            FileForm(
                id=file_id,
                filename=filename,
                path=file_path,
                meta={
                    "name": filename,
                    "content_type": "application/json",
                    "size": len(body),
                    "chat_id": chat_id,
                    SCOUT_RESULT_META_KEY: True,
                },
            ),
        )
    )
    return file_id


# ---------------------------------------------------------------------------
# Readers
# ---------------------------------------------------------------------------


async def read_artifact(result_id: str) -> Optional[dict]:
    """Read an artifact by file id. Returns the parsed payload, or None if the
    file is missing or unreadable. Raises nothing — callers decide how to fail."""
    if not result_id:
        return None

    try:
        from open_webui.models.files import Files
        from open_webui.storage.provider import Storage
    except Exception:
        log.exception("scout_artifacts: open_webui imports failed")
        return None

    try:
        file_record = await _maybe_await(Files.get_file_by_id(result_id))
        if file_record is None:
            return None
        path = getattr(file_record, "path", None)
        if not path:
            return None
        # Storage returns either a file-like, a bytes payload, or a path —
        # normalize to bytes via Storage.get_file (which the rest of OWUI
        # uses) when available.
        contents = await _maybe_await(Storage.get_file(path))
        if isinstance(contents, str):
            with open(contents, "rb") as fh:
                contents = fh.read()
        if hasattr(contents, "read"):
            contents = contents.read()
        return json.loads(contents)
    except Exception:
        log.exception("scout_artifacts: failed to read %s", result_id)
        return None


async def find_recent_artifacts(user_id: str, chat_id: str) -> list:
    """List Scout artifacts for this chat, newest first. Returns the OWUI file
    records (not the parsed payloads) — caller calls read_artifact on the ids
    it cares about."""
    if not user_id or not chat_id:
        return []
    try:
        from open_webui.models.files import Files
    except Exception:
        log.exception("scout_artifacts: Files import failed")
        return []

    all_files = await _maybe_await(Files.get_files_by_user_id(user_id))
    ours = [
        f
        for f in all_files
        if (f.meta or {}).get("chat_id") == chat_id
        and (f.meta or {}).get(SCOUT_RESULT_META_KEY)
    ]
    ours.sort(key=lambda f: f.created_at or 0, reverse=True)
    return ours


# ---------------------------------------------------------------------------
# Substitution
# ---------------------------------------------------------------------------


def pick_substitution_column(rows: list[dict]) -> Optional[str]:
    """Return the most specific identifier column present in rows[0], in
    preference order: message_control_id > accession_number > epic_mrn."""
    if not rows:
        return None
    first = rows[0] or {}
    for col in SUBSTITUTION_COLUMN_PREFERENCE:
        if col in first and first[col] is not None:
            return col
    return None


def substitute_cohort(sql: str, artifact: dict) -> str:
    """Replace the literal `{{cohort}}` token in `sql` with `<col> IN (...)`
    using the artifact's identifier rows. Filters to `included: true` rows
    when that flag is present (search artifacts); takes all rows otherwise
    (id-list artifacts). Errors loudly if `{{cohort}}` is absent — that's
    almost always a model mistake (an `in_cohort` was passed but the SQL has
    no placeholder)."""
    if COHORT_PLACEHOLDER not in sql:
        raise ValueError(
            f"in_cohort was set but SQL has no {COHORT_PLACEHOLDER!r} placeholder. "
            f"Add {COHORT_PLACEHOLDER} to your WHERE clause where the cohort filter belongs."
        )

    rows = artifact.get("rows") or []
    column = pick_substitution_column(rows)
    if not column:
        raise ValueError(
            "Cohort artifact has no recognizable identifier column "
            f"(expected one of {SUBSTITUTION_COLUMN_PREFERENCE})."
        )

    # If `included` is present anywhere, treat it as a search artifact and
    # filter — otherwise (uploaded id list) take everything.
    has_included_flag = any("included" in r for r in rows)
    if has_included_flag:
        values = [
            str(r[column])
            for r in rows
            if r.get("included") and r.get(column) is not None
        ]
    else:
        values = [str(r[column]) for r in rows if r.get(column) is not None]

    if not values:
        raise ValueError("Cohort artifact has no included rows to substitute.")

    fragment = _render_in_clause(column, values)
    return sql.replace(COHORT_PLACEHOLDER, fragment)


def _render_in_clause(column: str, values: list[str]) -> str:
    """Build `<col> IN ('a','b',...)` for small lists, or
    `<col> IN (SELECT v FROM (VALUES ('a'),('b'),...) AS t(v))` for large
    ones — same end result, friendlier to Trino's planner past ~5000 IDs."""
    quoted = [f"'{_sql_escape(v)}'" for v in values]
    if len(values) <= VALUES_SHAPE_THRESHOLD:
        return f"{column} IN ({', '.join(quoted)})"
    rows_clause = ", ".join(f"({q})" for q in quoted)
    return f"{column} IN (SELECT v FROM (VALUES {rows_clause}) AS t(v))"


def _sql_escape(value: str) -> str:
    """Single-quote escape for SQL literal context. We use string substitution
    rather than parameterized queries because the upstream tool composes its
    own SQL and Trino's HTTP API accepts text/plain — but we still need to
    defend against rogue values in identifier columns."""
    return value.replace("'", "''")


# ---------------------------------------------------------------------------
# LRU prune
# ---------------------------------------------------------------------------


async def prune_artifacts(user_id: str, chat_id: str, keep_n: int) -> None:
    """Delete all but the most recent N Scout artifacts for this chat. Safe to
    call as a fire-and-forget task — swallows errors."""
    try:
        from open_webui.models.files import Files
        from open_webui.storage.provider import Storage
    except Exception:
        log.exception("scout_artifacts: prune imports failed")
        return

    try:
        ours = await find_recent_artifacts(user_id, chat_id)
    except Exception:
        log.exception("scout_artifacts: prune list failed")
        return

    for old in ours[keep_n:]:
        try:
            await _maybe_await(Storage.delete_file(old.path))
        except Exception:
            log.exception("scout_artifacts: storage delete failed for %s", old.id)
        try:
            await _maybe_await(Files.delete_file_by_id(old.id))
        except Exception:
            log.exception("scout_artifacts: record delete failed for %s", old.id)
