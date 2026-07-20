"""Shared helpers for endpoints that ingest a researcher-supplied CSV
of identifiers - `/api/searches/from-file` and `/api/reports/query/from-file`.

Both endpoints share the same parse + dedup + column-resolve pipeline
and the same `{{cohort}}` placeholder substitution for LLM-authored
custom SQL. The parse produces a normalized ID list; substitute_cohort
lets each endpoint plug its own bound `contains(?, col)` predicate into
the LLM's SQL, and a persisted search stores the ID list so every later
read re-binds it.
"""

from __future__ import annotations

import csv
import io

from fastapi import HTTPException, status

from .models import (
    FILE_UPLOAD_HEADER_ALIASES,
    FILE_UPLOAD_ID_COLUMNS,
    PATIENT_ID_COLUMNS,
)


MAX_UPLOAD_BYTES = 32 * 1024 * 1024
MAX_UPLOAD_IDS = 10_000
UNMATCHED_SAMPLE_CAP = 20
COHORT_PLACEHOLDER = "{{cohort}}"


def guard_upload_size(raw: bytes) -> None:
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                f"upload exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MiB; "
                f"narrow the CSV or raise the limit if storage is verified."
            ),
        )


def parse_csv_ids(
    raw: bytes, requested_column: str | None
) -> tuple[list[str], str, bool]:
    """Return (ids, id_column, column_inferred). Raises 400 if the header
    yields no usable column."""
    # utf-8-sig strips a leading BOM (Excel exports include one).
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1", errors="replace")
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="empty file",
        )
    header = [c.strip().lower() for c in rows[0]]

    if requested_column:
        if requested_column not in FILE_UPLOAD_ID_COLUMNS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"id_column must be one of {list(FILE_UPLOAD_ID_COLUMNS)}; "
                    f"got {requested_column!r}"
                ),
            )
        id_column = requested_column
        column_inferred = False
    else:
        candidates = [c for c in FILE_UPLOAD_ID_COLUMNS if _header_matches(header, c)]
        if not candidates:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"couldn't infer id_column from header {rows[0]!r}; "
                    f"specify id_column explicitly"
                ),
            )
        id_column = candidates[0]
        column_inferred = True

    idx = _header_index(header, id_column)
    if idx is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(f"header {rows[0]!r} has no column matching {id_column!r}"),
        )
    ids = [r[idx].strip() for r in rows[1:] if idx < len(r) and r[idx].strip()]
    return ids, id_column, column_inferred


def dedup_ids(ids: list[str]) -> list[str]:
    seen: set[str] = set()
    cleaned: list[str] = []
    for s in ids:
        if s in seen:
            continue
        seen.add(s)
        cleaned.append(s)
    if len(cleaned) > MAX_UPLOAD_IDS:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                f"submitted ID list exceeds cap ({MAX_UPLOAD_IDS}); "
                f"narrow the upload."
            ),
        )
    if not cleaned:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="no usable IDs in file",
        )
    return cleaned


def resolve_sql_column(id_column: str) -> str:
    """Translate a user-facing id_column (epic_mrn) to the view column
    the WHERE clause should filter on (resolved_epic_mrn). Report-scoped
    columns pass through unchanged."""
    return PATIENT_ID_COLUMNS.get(id_column, id_column)


def assert_cohort_placeholder(sql: str) -> None:
    """Raise 400 unless `{{cohort}}` appears in `sql` exactly once."""
    count = sql.count(COHORT_PLACEHOLDER)
    if count == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"sql must include the {COHORT_PLACEHOLDER} placeholder "
                f"exactly once (would otherwise return the whole table)."
            ),
        )
    if count > 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"sql includes {COHORT_PLACEHOLDER} {count} times; must appear "
                f"exactly once."
            ),
        )


def substitute_cohort(sql: str, predicate: str) -> str:
    """Replace the single `{{cohort}}` placeholder in `sql` with
    `predicate`. Assumes assert_cohort_placeholder passed."""
    assert_cohort_placeholder(sql)
    return sql.replace(COHORT_PLACEHOLDER, predicate)


def _header_matches(header: list[str], id_column: str) -> bool:
    return _header_index(header, id_column) is not None


def _header_index(header: list[str], id_column: str) -> int | None:
    aliases = FILE_UPLOAD_HEADER_ALIASES[id_column]
    for i, h in enumerate(header):
        if any(a in h for a in aliases):
            return i
    return None
