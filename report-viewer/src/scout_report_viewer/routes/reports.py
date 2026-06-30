"""HTTP routes for `/api/reports` - RPC-style operations that don't
persist state. Used by `scout_query_sql` and `scout_get_reports` in
the OWUI tool, and by the SPA row-expand panel (which posts an array
of one ID to /api/reports/read)."""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from .. import metrics, trino_client
from ..auth import User, get_current_user
from ..config import settings
from ..models import (
    KNOWN_ID_COLUMNS,
    PATIENT_ID_COLUMNS,
    QueryRequest,
    QueryResponse,
    ReadReportsRequest,
    ReadReportsResponse,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/reports", tags=["reports"])


def _jsonsafe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return json.loads(json.dumps(rows, default=str))


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
    return QueryResponse(columns=columns, rows=_jsonsafe(rows))


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
    if body.id_column not in KNOWN_ID_COLUMNS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"id_column must be one of {list(KNOWN_ID_COLUMNS)}, got {body.id_column!r}",
        )
    if not body.id_column.replace("_", "").isalnum():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unsafe id_column: {body.id_column!r}",
        )
    # Report-scoped IDs hit reports_latest directly; patient-scoped IDs
    # route through reports_latest_epic_view (raw mrn/mpi mapped to
    # resolved_* under the hood).
    base = f"{settings.trino_catalog}.{settings.trino_schema}."
    if body.id_column in PATIENT_ID_COLUMNS:
        table = base + "reports_latest_epic_view"
        column = PATIENT_ID_COLUMNS[body.id_column]
    else:
        table = base + "reports_latest"
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
    rows = _jsonsafe(rows)
    return ReadReportsResponse(columns=columns, rows=rows)
