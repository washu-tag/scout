"""HTTP routes for `/api/reports` - RPC-style operations that don't
persist state. Used by `scout_query_sql` and `scout_get_reports` in
the OWUI tool, and by the SPA row-expand panel (which posts an array
of one ID to /api/reports/read)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from .. import metrics, trino_client
from ..auth import User, get_current_user
from ..config import settings
from ..csv_upload import (
    UNMATCHED_SAMPLE_CAP,
    assert_cohort_placeholder,
    dedup_ids,
    guard_upload_size,
    parse_csv_ids,
    resolve_sql_column,
    substitute_cohort,
)
from ..models import (
    INPUT_ID_COLUMNS,
    PATIENT_ID_COLUMNS,
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
    cleaned = dedup_ids(ids)

    sql_column = resolve_sql_column(resolved_id_column)
    if not sql_column.replace("_", "").isalnum():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unsafe id_column: {sql_column!r}",
        )
    col_q = f'"{sql_column}"'
    view = f"{settings.trino_catalog}.{settings.trino_schema}.reports_latest_epic_view"

    matched: set[str] = set()
    validate_sql = (
        f"SELECT DISTINCT {col_q} AS id FROM {view} WHERE contains(?, {col_q})"
    )
    try:
        with metrics.time_trino("query_from_file_validate"):
            _cols, vrows = await trino_client.execute(
                validate_sql, user=user.sub, params=[cleaned]
            )
    except Exception as exc:
        log.exception("trino id-list validation failed")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"trino validation failed: {exc}",
        )
    for r in vrows:
        v = r.get("id")
        if v is not None:
            matched.add(str(v))
    unmatched = [i for i in cleaned if i not in matched]

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

    return QueryFromFileResponse(
        columns=columns,
        rows=rows,
        id_column=resolved_id_column,
        column_inferred=column_inferred,
        unmatched=unmatched[:UNMATCHED_SAMPLE_CAP],
        unmatched_count=len(unmatched),
    )


@router.post(
    "/read",
    response_model=ReadReportsResponse,
    status_code=status.HTTP_200_OK,
)
async def read_reports(
    body: ReadReportsRequest,
    user: User = Depends(get_current_user),
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
    if not body.id_column.replace("_", "").isalnum():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unsafe id_column: {body.id_column!r}",
        )
    base = f"{settings.trino_catalog}.{settings.trino_schema}."
    table = base + "reports_latest_epic_view"
    if body.id_column in PATIENT_ID_COLUMNS:
        column = PATIENT_ID_COLUMNS[body.id_column]
    else:
        column = body.id_column
    # contains(?, col) - the driver doesn't expand list params into IN.
    sql = f'SELECT * FROM {table} WHERE contains(?, "{column}")'
    try:
        with metrics.time_trino("read_reports"):
            columns, rows = await trino_client.execute(
                sql, user=user.sub, params=[[str(i) for i in body.ids]]
            )
    except Exception as exc:
        log.exception("trino read_reports failed")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"trino query failed: {exc}",
        )
    return ReadReportsResponse(columns=columns, rows=rows)
