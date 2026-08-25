"""HTTP routes for `/api/plots`.

A chart is saved SQL plus a Vega-Lite spec. `GET` re-runs the SQL for the
iframe, so charts stay live and no result rows are stored, same bargain as a
saved search.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from .. import metrics, trino_client
from ..auth import User, get_current_user
from ..config import settings
from ..ids import new_plot_id
from ..models import PlotDetail, PlotRequest, PlotResponse
from ..store import PlotStore, get_plot_store

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/plots", tags=["plots"])

VEGA_LITE_SCHEMA = "https://vega.github.io/schema/vega-lite/v5.json"

# Never plottable, and the browser has no use for them here.
_HEAVY_COLS: frozenset[str] = frozenset(
    {
        "report_text",
        "report_section_findings",
        "report_section_impression",
        "report_section_addendum",
    }
)


# The viewer owns how a chart looks, and merges its own config at render.
# A model-supplied palette or size fights that, so drop them on the way in.
_COSMETIC_KEYS: frozenset[str] = frozenset(
    {"config", "background", "padding", "width", "height", "autosize"}
)


def _reject_foreign_urls(node: Any) -> None:
    """A `url` anywhere in the spec would make the renderer fetch off-origin."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "url":
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        "spec must not set any 'url'; omit `data` entirely and "
                        "the service attaches the rows it just queried"
                    ),
                )
            _reject_foreign_urls(value)
    elif isinstance(node, list):
        for item in node:
            _reject_foreign_urls(item)


def _validate_spec(spec: dict[str, Any]) -> None:
    if not isinstance(spec, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="vega_lite_spec must be a JSON object",
        )
    if not any(k in spec for k in ("mark", "layer", "facet", "hconcat", "vconcat")):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="vega_lite_spec needs a 'mark' (or layer/facet/concat)",
        )
    _reject_foreign_urls(spec)


@router.post("", response_model=PlotResponse, status_code=status.HTTP_200_OK)
async def create_plot(
    body: PlotRequest,
    user: User = Depends(get_current_user),
    store: PlotStore = Depends(get_plot_store),
) -> PlotResponse:
    _validate_spec(body.vega_lite_spec)

    # Run it now so a broken query fails while the model can still fix it.
    try:
        with metrics.time_trino("plot_query"):
            columns, rows = await trino_client.execute(body.sql, user=user.sub)
    except Exception as exc:
        log.exception("trino plot query failed")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"trino query failed: {exc}",
        )
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="query returned no rows, so there is nothing to chart",
        )
    metrics.RESULT_ROWS.labels(op="plot_query").observe(len(rows))

    lean_columns = [c for c in columns if c not in _HEAVY_COLS]
    spec = {k: v for k, v in body.vega_lite_spec.items() if k not in _COSMETIC_KEYS}
    spec["$schema"] = VEGA_LITE_SCHEMA
    spec.pop("data", None)

    plot_id = new_plot_id()
    await store.insert_plot(
        plot_id=plot_id,
        sql=body.sql,
        spec=spec,
        owner_sub=user.sub,
        sql_explanation=body.sql_explanation,
        owui_chat_id=body.owui_chat_id,
    )
    return PlotResponse(
        id=plot_id,
        view_url=f"{settings.external_url.rstrip('/')}/spa/plots/{plot_id}",
        columns=lean_columns,
        row_count=len(rows),
    )


@router.get("/{plot_id}", response_model=PlotDetail)
async def get_plot(
    plot_id: str,
    user: User = Depends(get_current_user),
    store: PlotStore = Depends(get_plot_store),
) -> PlotDetail:
    """Spec, freshly-evaluated rows, and the SQL behind them, for the SPA's
    chart route."""
    plot = await store.get_plot(plot_id, user.sub)
    if plot is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="chart not found"
        )
    try:
        with metrics.time_trino("plot_rows"):
            _cols, rows = await trino_client.execute(plot["sql"], user=user.sub)
    except Exception as exc:
        log.exception("trino plot rows failed")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"could not re-run this chart's query: {exc}",
        )
    cap = settings.max_cohort_rows
    lean_rows = [
        {k: v for k, v in r.items() if k not in _HEAVY_COLS} for r in rows[:cap]
    ]
    metrics.RESULT_ROWS.labels(op="plot_rows").observe(len(lean_rows))
    return PlotDetail(
        id=plot["id"],
        spec=plot["spec"],
        rows=lean_rows,
        sql=plot["sql"],
        sql_explanation=plot["sql_explanation"],
    )
