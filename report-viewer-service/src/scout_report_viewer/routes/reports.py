"""HTTP routes for `/reports` — RPC-style operations that don't persist
state. Used by `scout_query_sql` and `scout_get_reports` in the OWUI
tool, and by the SPA row-expand panel (which posts an array of one ID
to /reports/read)."""

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
    QueryRequest,
    QueryResponse,
    ReadReportsRequest,
    ReadReportsResponse,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/reports", tags=["reports"])


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
            columns, rows = await trino_client.execute(
                body.sql, user=user.sub, token=user.token
            )
    except Exception as exc:
        log.exception("trino query failed")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"trino query failed: {exc}",
        )
    truncated = len(rows) > body.row_cap
    if truncated:
        rows = rows[: body.row_cap]
    rows = _jsonsafe(rows)
    return QueryResponse(columns=columns, rows=rows, truncated=truncated)


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
    table = f"{settings.trino_catalog}.{settings.trino_schema}.reports_latest"
    # Safely-quoted literals — psycopg-style ? binding isn't supported by
    # the trino driver path here.
    quoted = ", ".join("'" + str(i).replace("'", "''") + "'" for i in body.ids)
    sql = f"SELECT * FROM {table} " f'WHERE "{body.id_column}" IN ({quoted})'
    try:
        with metrics.time_trino("read_reports"):
            columns, rows = await trino_client.execute(
                sql, user=user.sub, token=user.token
            )
    except Exception as exc:
        log.exception("trino read_reports failed")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"trino query failed: {exc}",
        )
    rows = _jsonsafe(rows)
    return ReadReportsResponse(columns=columns, rows=rows)
