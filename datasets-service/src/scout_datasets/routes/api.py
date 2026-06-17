"""SPA-facing API.

The ``/datasets`` router is shared with the OWUI tool path (LLM-driven
create / read / aggregate). The browser SPA lives behind a separate
``/api/...`` namespace for owner-scoped listing + metadata; row fetches
go through the capability-style ``/datasets/{id}/rows`` route since the
SPA renders inside the iframe viewer.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from .. import store
from ..auth import User, get_current_user
from ..models import DatasetMeta

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["api"])


@router.get("/datasets", response_model=list[DatasetMeta])
async def list_datasets_route(
    user: User = Depends(get_current_user),
) -> list[DatasetMeta]:
    """Caller's non-expired datasets, newest first. Drives the SPA
    homepage. Owner-scoped — an authenticated user only sees their own."""
    rows = await store.list_datasets(user.sub)
    return [_meta_from_row(r) for r in rows]


def _meta_from_row(r: dict[str, Any]) -> DatasetMeta:
    return DatasetMeta(
        dataset_id=r["dataset_id"],
        kind=r["kind"],
        id_column=r["id_column"],
        count=r["count"],
        parent_id=r["parent_id"],
        source_sql=r["source_sql"],
        owner_sub=r["owner_sub"],
        created_at=r["created_at"],
        expires_at=r["expires_at"],
        last_read_at=r["last_read_at"],
        highlight_terms=r.get("highlight_terms") or [],
        sql_explanation=r.get("sql_explanation") or "",
        owui_chat_id=r.get("owui_chat_id") or "",
        owui_chat_title=r.get("owui_chat_title") or "",
    )


@router.get("/datasets/{dataset_id}", response_model=DatasetMeta)
async def get_dataset_meta(
    dataset_id: str,
    user: User = Depends(get_current_user),
) -> DatasetMeta:
    """Single dataset's metadata. Owner-scoped — the SPA only shows you
    your own datasets."""
    ds = await store.get_dataset(dataset_id, owner_sub=user.sub)
    if ds is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return _meta_from_row(ds)
