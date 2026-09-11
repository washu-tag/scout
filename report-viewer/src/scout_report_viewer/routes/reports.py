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
    # contains(?, col) - the driver doesn't expand list params into IN.
    sql = f'SELECT * FROM {table} WHERE contains(?, "{column}")'
    try:
        with metrics.time_trino("read_reports"):
            # safe: table from READ_REPORTS_TABLES allowlist, column from
            # INPUT_ID_COLUMNS allowlist, IDs bind via ?
            # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
            columns, rows = await trino_client.execute(
                sql, user=user.sub, params=[[str(i) for i in body.ids]]
            )
    except Exception as exc:
        log.exception("trino read_reports failed")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"trino query failed: {exc}",
        )
    metrics.RESULT_ROWS.labels(op="read_reports").observe(len(rows))
    return ReadReportsResponse(
        columns=columns,
        rows=rows,
        highlights=await _highlights(body.search_id, rows, column, table, user, store),
    )


async def _highlights(
    search_id: str | None,
    rows: list[dict[str, Any]],
    id_column: str,
    table: str,
    user: User,
    store: SearchStore,
) -> list[list[Highlight]]:
    """Match offsets per row, computed by Trino from the search's own patterns.

    A separate query rather than extra columns on the read: highlighting is a
    nicety, and isolating it means a failure here cannot stop a report opening.
    Owner-scoped, so naming someone else's search yields nothing rather than
    leaking its patterns.
    """
    if not search_id or not rows:
        return []
    try:
        search = await store.get_search(search_id, owner_sub=user.sub)
        if search is None:
            return []
        expression = highlight_hits_expression(search["sql"])
        if expression is None:
            return []
        ids = [str(r.get(id_column)) for r in rows if r.get(id_column) is not None]
        if not ids:
            return []
        sql = (
            f'SELECT "{id_column}" AS _id, {expression} AS hits '
            f'FROM {table} WHERE contains(?, "{id_column}")'
        )
        with metrics.time_trino("read_reports_highlights"):
            # safe: table and id_column are allowlisted by the caller, the
            # patterns are quoted literals from persisted sql, ids bind via ?
            # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
            _cols, hit_rows = await trino_client.execute(
                sql, user=user.sub, params=[ids]
            )
    except Exception:
        log.exception("highlight query failed (non-fatal)")
        return []

    by_id: dict[str, list[Highlight]] = {}
    for hit_row in hit_rows:
        raw = hit_row.get("hits")
        parsed = json.loads(raw) if isinstance(raw, str) else (raw or [])
        by_id[str(hit_row.get("_id"))] = [
            # Trino positions are 1-based; the frontend slices from 0.
            Highlight(
                field=h["field"],
                start=h["pos"] - 1,
                end=h["pos"] - 1 + h["len"],
                polarity=h["polarity"],
            )
            for h in parsed
            if h.get("pos", 0) > 0 and h.get("len", 0) > 0
        ]
    return [by_id.get(str(r.get(id_column)), []) for r in rows]
