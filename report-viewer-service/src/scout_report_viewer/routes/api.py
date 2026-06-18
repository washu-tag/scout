"""SPA-facing API.

Owner-scoped list + single-search metadata for the browser SPA. Row
fetches go through the capability-style ``/searches/{id}/rows`` route
since the SPA renders inside the iframe viewer.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from .. import store
from ..auth import User, get_current_user
from ..models import SearchMeta

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["api"])


@router.get("/searches", response_model=list[SearchMeta])
async def list_searches_route(
    user: User = Depends(get_current_user),
) -> list[SearchMeta]:
    """Caller's non-expired searches, newest first. Drives the SPA
    homepage. Owner-scoped — an authenticated user only sees their own."""
    rows = await store.list_searches(user.sub)
    return [_meta_from_row(r) for r in rows]


def _meta_from_row(r: dict[str, Any]) -> SearchMeta:
    return SearchMeta(
        id=r["id"],
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


@router.get("/searches/{search_id}", response_model=SearchMeta)
async def get_search_meta(
    search_id: str,
    user: User = Depends(get_current_user),
) -> SearchMeta:
    """Single search's metadata. Owner-scoped — the SPA only shows you
    your own searches."""
    s = await store.get_search(search_id, owner_sub=user.sub)
    if s is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return _meta_from_row(s)
