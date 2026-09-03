"""HTTP routes for `/api/plots`.

A chart is saved SQL plus a Vega-Lite spec. `GET /{id}` re-runs the SQL for
the iframe, so charts stay live and no result rows are stored, same bargain as
a saved search. `GET /api/plots` lists the caller's charts for the SPA
homepage, which shows them next to saved searches.

`POST /from-file` charts an uploaded CSV cohort; its ID list is stored with the
chart so every re-run binds the same rows.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import vl_convert
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)

from .. import metrics, trino_client
from ..auth import User, get_current_user
from ..config import settings
from ..csv_upload import (
    assert_cohort_placeholder,
    dedup_ids,
    guard_upload_size,
    parse_csv_ids,
    quote_ident,
    substitute_cohort,
)
from ..ids import new_plot_id
from ..models import PlotDetail, PlotMeta, PlotRequest, PlotResponse
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


_SCALE_PALETTE_KEYS: frozenset[str] = frozenset({"scheme", "range"})


def _strip_scale_palette(node: Any) -> Any:
    """A nested `encoding.*.scale.scheme` fights the theme the same way a
    top-level `config` does, but _COSMETIC_KEYS only strips top-level keys."""
    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            if key == "scale" and isinstance(value, dict):
                value = {k: v for k, v in value.items() if k not in _SCALE_PALETTE_KEYS}
            out[key] = _strip_scale_palette(value)
        return out
    if isinstance(node, list):
        return [_strip_scale_palette(item) for item in node]
    return node


def _clean_spec(raw_spec: dict[str, Any]) -> dict[str, Any]:
    """Strip viewer-owned/palette keys once, before validating, so the
    render check covers what actually gets persisted, not the raw input."""
    spec = {k: v for k, v in raw_spec.items() if k not in _COSMETIC_KEYS}
    return _strip_scale_palette(spec)


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


def _reject_bad_legend_bind(node: Any) -> None:
    """Valid form is `"bind": "legend"`, not `{"legend": true}`."""
    if isinstance(node, dict):
        bind = node.get("bind")
        if isinstance(bind, dict) and bind.get("legend") is True:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    'bind must be the string "legend", not '
                    '{"legend": true} - use "bind": "legend" on the param'
                ),
            )
        for value in node.values():
            _reject_bad_legend_bind(value)
    elif isinstance(node, list):
        for item in node:
            _reject_bad_legend_bind(item)


def _reject_uncompilable_spec(spec: dict[str, Any]) -> None:
    """Catch-all: actually render the spec rather than just inspecting it."""
    try:
        vl_convert.vegalite_to_svg(spec)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"vega_lite_spec failed to render: {exc}",
        )


def _parse_spec_form(raw: str) -> dict[str, Any]:
    """The spec rides as JSON text; a multipart body can't carry a JSON object."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"vega_lite_spec must be valid JSON: {exc}",
        )


def _validate_spec(spec: dict[str, Any]) -> None:
    if not isinstance(spec, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="vega_lite_spec must be a JSON object",
        )
    if not any(
        k in spec
        for k in ("mark", "layer", "facet", "hconcat", "vconcat", "concat", "repeat")
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="vega_lite_spec needs a 'mark' (or layer/facet/concat/repeat)",
        )
    _reject_foreign_urls(spec)
    _reject_bad_legend_bind(spec)
    _reject_uncompilable_spec(spec)


@router.get("", response_model=list[PlotMeta])
async def list_plots(
    user: User = Depends(get_current_user),
    store: PlotStore = Depends(get_plot_store),
) -> list[PlotMeta]:
    """Caller's charts, newest first. The SPA homepage lists these alongside
    saved searches. Owner-scoped - only the authenticated user's own."""
    rows = await store.list_plots(user.sub)
    return [
        PlotMeta(
            id=r["id"],
            sql=r["sql"],
            owner_sub=r["owner_sub"],
            created_at=r["created_at"],
            sql_explanation=r.get("sql_explanation") or "",
            owui_chat_id=r.get("owui_chat_id") or "",
        )
        for r in rows
    ]


async def _run_chart_query(
    sql: str,
    *,
    user_sub: str,
    op: str,
    params: list | None = None,
    hint: str = "",
) -> tuple[list[str], list[dict[str, Any]]]:
    """Run it now so a broken query fails while the model can still fix it.
    `hint` is appended to that error."""
    try:
        with metrics.time_trino(op):
            columns, rows = await trino_client.execute(
                sql, user=user_sub, params=params
            )
    except Exception as exc:
        log.exception("trino plot query failed")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"trino query failed: {exc}{hint}",
        )
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="query returned no rows, so there is nothing to chart",
        )
    metrics.RESULT_ROWS.labels(op=op).observe(len(rows))
    return columns, rows


async def _save_chart(
    store: PlotStore,
    *,
    sql: str,
    raw_spec: dict[str, Any],
    columns: list[str],
    row_count: int,
    user_sub: str,
    sql_explanation: str | None,
    owui_chat_id: str | None,
    uploaded_ids: list[str] | None = None,
) -> PlotResponse:
    """Persist the already-cleaned spec, return the view URL."""
    spec = dict(raw_spec)
    spec["$schema"] = VEGA_LITE_SCHEMA
    spec.pop("data", None)

    plot_id = new_plot_id()
    await store.insert_plot(
        plot_id=plot_id,
        sql=sql,
        spec=spec,
        owner_sub=user_sub,
        sql_explanation=sql_explanation,
        owui_chat_id=owui_chat_id,
        uploaded_ids=uploaded_ids,
    )
    return PlotResponse(
        id=plot_id,
        view_url=f"{settings.external_url.rstrip('/')}/spa/plots/{plot_id}",
        columns=[c for c in columns if c not in _HEAVY_COLS],
        row_count=row_count,
    )


@router.post("", response_model=PlotResponse, status_code=status.HTTP_200_OK)
async def create_plot(
    body: PlotRequest,
    user: User = Depends(get_current_user),
    store: PlotStore = Depends(get_plot_store),
) -> PlotResponse:
    spec = _clean_spec(body.vega_lite_spec)
    _validate_spec(spec)
    columns, rows = await _run_chart_query(body.sql, user_sub=user.sub, op="plot_query")
    return await _save_chart(
        store,
        sql=body.sql,
        raw_spec=spec,
        columns=columns,
        row_count=len(rows),
        user_sub=user.sub,
        sql_explanation=body.sql_explanation,
        owui_chat_id=body.owui_chat_id,
    )


@router.post("/from-file", response_model=PlotResponse, status_code=status.HTTP_200_OK)
async def create_plot_from_file(
    file: UploadFile = File(...),
    sql: str = Form(...),
    vega_lite_spec: str = Form(...),
    sql_explanation: str | None = Form(default=None),
    id_column: str | None = Form(default=None),
    owui_chat_id: str | None = Form(default=None),
    user: User = Depends(get_current_user),
    store: PlotStore = Depends(get_plot_store),
) -> PlotResponse:
    """Chart a cohort uploaded as a CSV of identifiers. Backs `scout_chart_sql`
    in file mode.

    `sql` must include `{{cohort}}` exactly once, and unlike
    `/api/searches/from-file` it is required: a chart has no default aggregate.
    The deduped ID list is stored with the chart, so later views re-run against
    the same cohort.
    """
    spec = _clean_spec(_parse_spec_form(vega_lite_spec))
    _validate_spec(spec)
    try:
        raw = await file.read()
    finally:
        await file.close()
    guard_upload_size(raw)
    assert_cohort_placeholder(sql)
    ids, resolved_id_column, _column_inferred = parse_csv_ids(raw, id_column)
    cleaned = dedup_ids(ids, resolved_id_column)

    predicate = f"contains(?, {quote_ident(resolved_id_column)})"
    chart_sql = substitute_cohort(sql, predicate)
    columns, rows = await _run_chart_query(
        chart_sql,
        user_sub=user.sub,
        op="plot_query_from_file",
        params=[cleaned],
        hint=f". Your SQL (before {{{{cohort}}}} substitution): {sql}",
    )
    return await _save_chart(
        store,
        sql=chart_sql,
        raw_spec=spec,
        columns=columns,
        row_count=len(rows),
        user_sub=user.sub,
        sql_explanation=sql_explanation,
        owui_chat_id=owui_chat_id,
        uploaded_ids=cleaned,
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
    uploaded_ids = plot.get("uploaded_ids")
    try:
        with metrics.time_trino("plot_rows"):
            _cols, rows = await trino_client.execute(
                plot["sql"],
                user=user.sub,
                params=[uploaded_ids] if uploaded_ids else None,
            )
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
