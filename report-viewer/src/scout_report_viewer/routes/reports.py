"""HTTP routes for `/api/reports` - RPC-style operations that don't
persist state. Used by `scout_query_sql` and `scout_get_reports` in
the OWUI tool, and by the SPA row-expand panel (which posts an array
of one ID to /api/reports/read)."""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from .. import metrics, trino_client
from ..auth import User, get_current_user
from ..config import settings
from ..evidence import highlight_hits_expression
from ..store import SearchStore, get_store
from ..csv_upload import (
    assert_cohort_placeholder,
    dedup_ids,
    guard_upload_size,
    parse_csv_ids,
    quote_ident,
    substitute_cohort,
)
from ..models import (
    Highlight,
    DEFAULT_READ_REPORTS_TABLE,
    EPIC_VIEW_TABLES,
    INPUT_ID_COLUMNS,
    READ_REPORTS_TABLES,
    QueryFromFileResponse,
    QueryRequest,
    QueryResponse,
    ReadReportsRequest,
    ReadReportsResponse,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.post(
    "/query",
    response_model=QueryResponse,
    status_code=status.HTTP_200_OK,
)
async def query_reports(
    body: QueryRequest,
    user: User = Depends(get_current_user),
) -> QueryResponse:
    """Run SQL once, return rows directly. No search persisted, no
    iframe rendered. Backs `scout_query_sql` in the OWUI tool."""
    try:
        with metrics.time_trino("query_reports"):
            columns, rows = await trino_client.execute(body.sql, user=user.sub)
    except Exception as exc:
        log.exception("trino query failed")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"trino query failed: {exc}",
        )
    metrics.RESULT_ROWS.labels(op="query_reports").observe(len(rows))
    return QueryResponse(columns=columns, rows=rows)


@router.post(
    "/query/from-file",
    response_model=QueryFromFileResponse,
    status_code=status.HTTP_200_OK,
)
async def query_from_file(
    file: UploadFile = File(...),
    sql: str = Form(...),
    id_column: str | None = Form(default=None),
    user: User = Depends(get_current_user),
) -> QueryFromFileResponse:
    """One-shot Trino query scoped to a CSV cohort. `sql` must include
    `{{cohort}}` exactly once; backend substitutes the appropriate
    `IN (...)` predicate. Nothing persists. Backs `scout_query_sql` in
    file mode."""
    try:
        raw = await file.read()
    finally:
        await file.close()
    guard_upload_size(raw)
    assert_cohort_placeholder(sql)
    ids, resolved_id_column, column_inferred = parse_csv_ids(raw, id_column)
    cleaned = dedup_ids(ids, resolved_id_column)

    col_q = quote_ident(resolved_id_column)

    # All IDs are bound; the custom SQL's own predicate decides what matches.
    predicate = f"contains(?, {col_q})"
    query_sql = substitute_cohort(sql, predicate)
    try:
        with metrics.time_trino("query_from_file"):
            columns, rows = await trino_client.execute(
                query_sql, user=user.sub, params=[cleaned]
            )
    except Exception as exc:
        log.exception("trino query-from-file failed")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"trino query failed: {exc}. Your SQL (before "
                f"{{{{cohort}}}} substitution): {sql}"
            ),
        )

    metrics.RESULT_ROWS.labels(op="query_from_file").observe(len(rows))
    return QueryFromFileResponse(
        columns=columns,
        rows=rows,
        id_column=resolved_id_column,
        column_inferred=column_inferred,
    )


@router.post(
    "/read",
    response_model=ReadReportsResponse,
    status_code=status.HTTP_200_OK,
)
async def read_reports(
    body: ReadReportsRequest,
    user: User = Depends(get_current_user),
    store: SearchStore = Depends(get_store),
) -> ReadReportsResponse:
    """Fetch full content of specific reports by ID. Backs
    `scout_get_reports` (LLM context) and the SPA row-expand panel
    (which sends an array of one ID)."""
    if not body.ids:
        return ReadReportsResponse(columns=[], rows=[])
    if body.id_column not in INPUT_ID_COLUMNS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"id_column must be one of {list(INPUT_ID_COLUMNS)}, got {body.id_column!r}",
        )
    table_name = body.table or DEFAULT_READ_REPORTS_TABLE
    if table_name not in READ_REPORTS_TABLES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"table must be one of {list(READ_REPORTS_TABLES)}, got {table_name!r}",
        )
    is_epic_view = table_name in EPIC_VIEW_TABLES
    # scout_patient_id exists only on epic views; guard for a clean 400 instead
    # of a Trino "column not found".
    if body.id_column == "scout_patient_id" and not is_epic_view:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "scout_patient_id lookups require an epic-view table "
                "(reports_curated_epic_view or reports_latest_epic_view)"
            ),
        )
    column = body.id_column
    base = f"{settings.trino_catalog}.{settings.trino_schema}."
    table = base + table_name
    # Highlight offsets ride along on this read. The standalone query had the
    # same table and predicate, so it doubled the cost of opening a report.
    hits_expression = await _highlight_expression(body.search_id, user, store)
    ids = [str(i) for i in body.ids]
    # One placeholder per ID: the driver won't expand a list param, and
    # contains(?, col) defeats Delta stats-based file skipping.
    placeholders = ", ".join("?" * len(ids))

    async def read(projection: str) -> tuple[list[str], list[dict[str, Any]]]:
        sql = f'SELECT {projection} FROM {table} WHERE "{column}" IN ({placeholders})'
        with metrics.time_trino("read_reports"):
            # safe: table from READ_REPORTS_TABLES allowlist, column from
            # INPUT_ID_COLUMNS allowlist, IDs bind via ?
            # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
            return await trino_client.execute(sql, user=user.sub, params=ids)

    def failed(exc: Exception) -> HTTPException:
        log.exception("trino read_reports failed")
        return HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"trino query failed: {exc}",
        )

    try:
        columns, rows = await read(
            f"*, {hits_expression} AS {_HITS_COLUMN}" if hits_expression else "*"
        )
    except Exception as exc:
        if not hits_expression or not trino_client.is_retryable(exc):
            raise failed(exc)
        # Never lose a report to a reviewing aid.
        log.exception("highlight projection failed; re-reading without it")
        hits_expression = None
        try:
            columns, rows = await read("*")
        except Exception as retry_exc:
            raise failed(retry_exc)

    highlights = _parse_hits(rows) if hits_expression else []
    columns = [c for c in columns if c != _HITS_COLUMN]
    for row in rows:
        row.pop(_HITS_COLUMN, None)

    metrics.RESULT_ROWS.labels(op="read_reports").observe(len(rows))
    return ReadReportsResponse(columns=columns, rows=rows, highlights=highlights)


_HITS_COLUMN = "ev_hits"


async def _highlight_expression(
    search_id: str | None, user: User, store: SearchStore
) -> str | None:
    """The search's match-offset expression, or None.

    Owner-scoped, so naming someone else's search yields nothing rather than
    leaking its patterns.
    """
    if not search_id:
        return None
    try:
        search = await store.get_search(search_id, owner_sub=user.sub)
        return highlight_hits_expression(search["sql"]) if search else None
    except Exception:
        log.exception("highlight expression lookup failed (non-fatal)")
        return None


def _one_hit(h: Any) -> Highlight | None:
    """One hit, however Trino encoded the row.

    A named ROW casts to a JSON object on most versions and to a positional
    array on others, so accept both rather than lose every mark to the shape.
    Positions are 1-based; the frontend slices from 0.
    """
    if isinstance(h, dict):
        field, pos, length, polarity = (
            h.get("field"),
            h.get("pos"),
            h.get("len"),
            h.get("polarity"),
        )
    elif isinstance(h, (list, tuple)) and len(h) >= 4:
        field, pos, length, polarity = h[0], h[1], h[2], h[3]
    else:
        return None
    if not field or not isinstance(pos, int) or not isinstance(length, int):
        return None
    if pos < 1 or length < 1:
        return None
    return Highlight(
        field=str(field),
        start=pos - 1,
        end=pos - 1 + length,
        polarity=str(polarity or "positive"),
    )


def _parse_hits(rows: list[dict[str, Any]]) -> list[list[Highlight]]:
    out: list[list[Highlight]] = []
    for row in rows:
        raw = row.get(_HITS_COLUMN)
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else (raw or [])
            hits = [h for h in (_one_hit(x) for x in parsed) if h is not None]
        except Exception:
            # An unexpected encoding costs the marks, not the report.
            log.exception("highlight parse failed (non-fatal)")
            hits = []
        out.append(hits)
    return out
