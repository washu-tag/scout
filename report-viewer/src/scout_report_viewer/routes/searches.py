"""HTTP routes for `/api/searches` (V1.1 — just-in-time SQL evaluation).

A search is a saved SQL query plus minimal metadata. Nothing about
which rows match is stored. Every read wraps `sql` as a
subquery and applies pagination/sort/filter at the Trino layer.

See ADR 0026.

Endpoints:
  POST /api/searches                            — save SQL, cache COUNT(*), return sample
  POST /api/searches/from-file                  — validate IDs against reports_latest, save WHERE id IN (...) SQL
  GET  /api/searches/{id}                       — metadata
  GET  /api/searches/{id}/rows                  — paginated rows (wraps sql)
  GET  /api/searches/{id}/accessions            — DISTINCT accession_number list
  GET  /api/searches/{id}/csv                   — streaming CSV download

Single-report reads go through POST /api/reports/read (see routes/reports.py).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse

from .. import metrics, store, trino_client
from ..auth import User, get_current_user
from ..config import settings
from ..ids import new_search_id
from ..models import (
    KNOWN_ID_COLUMNS,
    CreateSearchRequest,
    CreateSearchResponse,
    CreateFromFileRequest,
    CreateFromFileResponse,
    SearchMeta,
    RowsResponse,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/searches", tags=["searches"])


# Safety cap on submitted IDs for /api/searches/from-file. The same cap
# becomes the upper bound on the IN-clause length in the saved SQL.
_MAX_FROM_FILE = 1_000_000

_LLM_SAMPLE_ROWS = 5


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _pick_id_column(columns: list[str], override: str | None) -> str:
    if override:
        if override not in columns:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"id_column {override!r} not in SELECT columns: {columns}",
            )
        return override
    for cand in KNOWN_ID_COLUMNS:
        if cand in columns:
            return cand
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=(
            "SELECT must include one of "
            f"{list(KNOWN_ID_COLUMNS)} (or pass id_column explicitly). "
            f"Got columns: {columns}"
        ),
    )


# Trino driver doesn't param-bind identifiers, so we interpolate
# column/table names through here. Literal values bind via `?` in
# trino_client.execute() — see /from-file for the one exception.
def _quote_ident(name: str) -> str:
    if not name.replace("_", "").isalnum():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unsafe identifier: {name!r}",
        )
    return f'"{name}"'


def _qualified_reports() -> str:
    return f"{settings.trino_catalog}.{settings.trino_schema}.reports_latest"


def _view_url(search_id: str) -> str:
    return f"{settings.external_url.rstrip('/')}/spa/searches/{search_id}"


def _jsonsafe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Coerce Trino-native types (datetime, Decimal) to JSON-safe scalars
    so pydantic v2's serializer accepts them. One round-trip through
    json.dumps(default=str) collapses every leaf."""
    return json.loads(json.dumps(rows, default=str))


def _wrap_sql(sql: str, *, alias: str = "src") -> str:
    """Strip trailing semicolons so the SQL can be embedded as a
    subquery: `SELECT ... FROM (<sql>) <alias>`. The LLM
    sometimes ends its SQL with a semicolon which breaks subquery
    syntax."""
    return sql.rstrip().rstrip(";")


# ---------------------------------------------------------------------------
# GET /api/searches — owner-scoped list (drives the SPA homepage)
# ---------------------------------------------------------------------------


def _meta_from_row(r: dict[str, Any]) -> SearchMeta:
    return SearchMeta(
        id=r["id"],
        id_column=r["id_column"],
        count=r["count"],
        sql=r["sql"],
        owner_sub=r["owner_sub"],
        created_at=r["created_at"],
        highlight_terms=r.get("highlight_terms") or [],
        highlight_diagnosis=r.get("highlight_diagnosis") or [],
        sql_explanation=r.get("sql_explanation") or "",
        owui_chat_id=r.get("owui_chat_id") or "",
    )


@router.get("", response_model=list[SearchMeta])
async def list_searches(
    user: User = Depends(get_current_user),
) -> list[SearchMeta]:
    """Caller's searches, newest first. Drives the SPA homepage.
    Owner-scoped — only the authenticated user's own."""
    rows = await store.list_searches(user.sub)
    return [_meta_from_row(r) for r in rows]


# ---------------------------------------------------------------------------
# POST /api/searches — save SQL, cache COUNT, return sample
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=CreateSearchResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_search(
    body: CreateSearchRequest,
    user: User = Depends(get_current_user),
) -> CreateSearchResponse:
    """Save a SQL query as a search. No row materialization — runs one
    `SELECT COUNT(*)` to cache the count, fetches 5 sample rows for
    the LLM, and (if highlight_terms is set) one additional small
    query against reports_latest to attach snippet + positive_dx
    fields to the sample.

    Refinement: when the LLM wants to narrow a search, it writes a new
    `POST /searches` call with the original conditions plus the new
    constraint — the saved SQL is standalone, no placeholder
    substitution, no parent reference required.
    """
    sql = _wrap_sql(body.sql)

    # Validate the search SQL by fetching the first 5 sample rows.
    # This both surfaces SQL errors early and gives us the sample
    # we need for the LLM-bound summary. The LIMIT lives outside the
    # sql we save — we wrap as a subquery so the LLM's own
    # LIMIT (e.g. LIMIT 50000) is respected on later /rows reads.
    sample_sql = f"SELECT s.* FROM ({sql}) s LIMIT {_LLM_SAMPLE_ROWS}"
    try:
        with metrics.time_trino("create_sample_query"):
            columns, sample_rows = await trino_client.execute(sample_sql, user=user.sub)
    except Exception as exc:
        log.exception("trino sample query failed")
        metrics.SEARCHES_CREATED.labels(
            id_column=body.id_column or "?", result="error"
        ).inc()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"trino query failed: {exc}",
        )
    if not sample_rows:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="query returned no rows — try broadening the filter.",
        )

    id_column = _pick_id_column(columns, body.id_column)

    # Cached COUNT(*) — separate Trino call. Cheap on top of the
    # sample query because Trino caches the inner subquery's predicate
    # execution between same-session calls of the same shape (and
    # because the optimizer recognizes COUNT-only subquery patterns).
    count_sql = f"SELECT COUNT(*) AS n FROM ({sql}) s"
    try:
        with metrics.time_trino("create_count_query"):
            _cols, count_rows = await trino_client.execute(count_sql, user=user.sub)
        row_count = int(count_rows[0]["n"]) if count_rows else 0
    except Exception as exc:
        log.exception("trino count query failed")
        # Non-fatal: search still gets created, just without cached
        # count. Reads will report 0 until the next refresh, which is
        # better than failing the whole create.
        row_count = 0

    # Pure LLM-context aid: one small Trino fetch for the sample IDs,
    # used to attach snippet (text-match) + positive_dx (chip-match)
    # to the sample rows. Nothing persisted.
    sample_extras: dict[str, dict[str, Any]] = {}
    if body.highlight_terms or body.highlight_diagnosis:
        sample_ids = [
            str(r.get(id_column)) for r in sample_rows if r.get(id_column) is not None
        ]
        if sample_ids:
            col_q = _quote_ident(id_column)
            extras_sql = (
                f"SELECT {col_q} AS _id, "
                f"report_section_impression, report_section_findings, "
                f"report_text, diagnoses "
                f"FROM {_qualified_reports()} "
                f"WHERE contains(?, {col_q})"
            )
            try:
                with metrics.time_trino("sample_text_fetch"):
                    _cols, ex_rows = await trino_client.execute(
                        extras_sql, user=user.sub, params=[sample_ids]
                    )
                for er in ex_rows:
                    key = er.get("_id")
                    if key is not None:
                        sample_extras[str(key)] = er
            except Exception:
                # Snippet feedback is a nice-to-have; carry on without.
                log.exception("sample-text fetch failed (non-fatal)")

    # \b boundaries so short tokens like "PE" don't match in "pectoralis".
    hl_pattern = None
    if body.highlight_terms:
        atoms = [re.escape(t.strip()) for t in body.highlight_terms if t and t.strip()]
        if atoms:
            hl_pattern = re.compile(r"(?is)\b(" + "|".join(atoms) + r")\b")

    # Strip SQL-LIKE `%` so the LLM can pass `R91` or `R91%` — same thing.
    dx_prefixes: list[str] = []
    if body.highlight_diagnosis:
        for d in body.highlight_diagnosis:
            if d and d.strip():
                dx_prefixes.append(d.strip().rstrip("%").lower())

    _drop_cols = {
        "report_text",
        "report_section_findings",
        "report_section_impression",
        "report_section_addendum",
    }
    sample = []
    for r in sample_rows:
        row_out = {k: v for k, v in r.items() if k not in _drop_cols}
        if body.highlight_terms or dx_prefixes:
            key = str(r.get(id_column)) if r.get(id_column) is not None else None
            extra = sample_extras.get(key, {}) if key else {}
            merged = {**r, **extra}
            if body.highlight_terms:
                snip = _extract_snippet(merged, body.highlight_terms)
                if snip:
                    row_out["snippet"] = snip
            dxs = extra.get("diagnoses") or r.get("diagnoses") or []
            positive_dx: list[dict[str, str]] = []
            for d in dxs if isinstance(dxs, list) else []:
                if not isinstance(d, dict):
                    continue
                code = str(d.get("diagnosis_code") or "")
                text = str(d.get("diagnosis_code_text") or "")
                if not code:
                    continue
                code_lc = code.lower()
                matched = False
                if dx_prefixes and any(code_lc.startswith(p) for p in dx_prefixes):
                    matched = True
                elif hl_pattern and hl_pattern.search(f"{code} {text}"):
                    matched = True
                if matched:
                    positive_dx.append({"code": code, "text": text})
            if positive_dx:
                row_out["positive_dx"] = positive_dx
        sample.append(row_out)
    sample = _jsonsafe(sample)

    search_id = new_search_id()
    stored = await store.insert_search(
        search_id=search_id,
        id_column=id_column,
        sql=sql,
        owner_sub=user.sub,
        row_count=row_count,
        highlight_terms=body.highlight_terms or [],
        highlight_diagnosis=body.highlight_diagnosis or [],
        sql_explanation=body.sql_explanation or "",
        owui_chat_id=body.owui_chat_id or "",
    )

    metrics.SEARCHES_CREATED.labels(id_column=id_column, result="ok").inc()
    metrics.SEARCH_SIZE.labels(source="sql").observe(stored["count"])
    log.info(
        "search created",
        extra={
            "search_id": stored["id"],
            "count": stored["count"],
            "id_column": id_column,
            "user_sub": user.sub,
        },
    )

    summary = _build_summary(
        total_rows=stored["count"],
        columns=columns,
        sample_rows=sample,
        saved_search_id=stored["id"],
    )

    return CreateSearchResponse(
        id=stored["id"],
        count=stored["count"],
        id_column=stored["id_column"],
        sample=sample,
        view_url=_view_url(search_id),
        summary=summary,
    )


# ---------------------------------------------------------------------------
# POST /api/searches/from-file — validate IDs, save WHERE id IN (...) SQL
# ---------------------------------------------------------------------------


@router.post(
    "/from-file",
    response_model=CreateFromFileResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_search_from_file(
    body: CreateFromFileRequest,
    user: User = Depends(get_current_user),
) -> CreateFromFileResponse:
    """Materialize a search from a researcher-supplied ID list. The
    OWUI tool reads the uploaded CSV/TSV, extracts the IDs, and POSTs
    them here. We validate each id against reports_latest and save a
    `WHERE <id_col> IN ('a', 'b', ...)` SQL as the search's source.

    Same downstream shape as POST /api/searches — the saved SQL is
    what /rows / /accessions / /csv re-run on each read."""
    if body.id_column not in KNOWN_ID_COLUMNS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"id_column must be one of {list(KNOWN_ID_COLUMNS)}; "
                f"got {body.id_column!r}"
            ),
        )

    # Dedup + strip whitespace + drop blanks. Preserve insertion order.
    seen: set[str] = set()
    cleaned: list[str] = []
    for raw in body.ids:
        if raw is None:
            continue
        s = str(raw).strip()
        if not s or s in seen:
            continue
        seen.add(s)
        cleaned.append(s)
    submitted = len(body.ids)
    if not cleaned:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="no usable IDs in submission (all blank / duplicates)",
        )
    if len(cleaned) > _MAX_FROM_FILE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                f"submitted ID list exceeds cap ({_MAX_FROM_FILE}); "
                f"narrow the upload or raise the limit if storage is verified."
            ),
        )

    # Validate ID existence: chunked SELECTs against reports_latest.
    qualified = _qualified_reports()
    col = _quote_ident(body.id_column)
    matched: set[str] = set()
    CHUNK = 5000
    sql = f"SELECT DISTINCT {col} AS id FROM {qualified} " f"WHERE contains(?, {col})"
    for start in range(0, len(cleaned), CHUNK):
        chunk = cleaned[start : start + CHUNK]
        try:
            with metrics.time_trino("from_file_validate"):
                _cols, rows = await trino_client.execute(
                    sql, user=user.sub, params=[chunk]
                )
        except Exception as exc:
            log.exception("trino id-list validation failed")
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"trino validation failed: {exc}",
            )
        for r in rows:
            v = r.get("id")
            if v is not None:
                matched.add(str(v))

    final_ids = [i for i in cleaned if i in matched]
    unmatched = [i for i in cleaned if i not in matched]
    if not final_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"none of the {len(cleaned)} submitted IDs matched "
                f"{body.id_column} in reports_latest"
            ),
        )

    # Compose the saved search SQL. IDs are inlined as literals (not
    # bound) because the SQL is persisted to Postgres as text and
    # replayed later by /rows /accessions /csv as a subquery — there
    # is no read-time params plumbing. This is the only place the
    # service interpolates user-supplied values into SQL text; every
    # other Trino call goes through `?` binding. If you find yourself
    # reaching for inline literal quoting elsewhere, you want
    # trino_client.execute(..., params=[...]) instead.
    in_literal = ", ".join("'" + str(i).replace("'", "''") + "'" for i in final_ids)
    sql = (
        f"SELECT message_control_id, accession_number, "
        f"resolved_epic_mrn AS epic_mrn, modality, service_name, "
        f"message_dt, patient_age, sex "
        f"FROM reports_latest_epic_view "
        f"WHERE {col} IN ({in_literal})"
    )
    row_count = len(final_ids)

    search_id = new_search_id()
    stored = await store.insert_search(
        search_id=search_id,
        id_column=body.id_column,
        sql=sql,
        owner_sub=user.sub,
        row_count=row_count,
        sql_explanation=body.sql_explanation or "",
        owui_chat_id=body.owui_chat_id or "",
    )

    metrics.SEARCHES_CREATED.labels(id_column=body.id_column, result="ok").inc()
    metrics.SEARCH_SIZE.labels(source="from_file").observe(stored["count"])
    metrics.IDS_SUBMITTED.labels(id_column=body.id_column).inc(submitted)
    metrics.IDS_UNMATCHED.labels(id_column=body.id_column).inc(len(unmatched))
    log.info(
        "search imported from file",
        extra={
            "search_id": stored["id"],
            "submitted": submitted,
            "matched": len(final_ids),
            "unmatched": len(unmatched),
        },
    )

    summary_lines = [
        f"Imported {len(final_ids):,} of {submitted:,} submitted {body.id_column} values.",
    ]
    if unmatched:
        summary_lines.append(
            f"{len(unmatched):,} IDs were not found in reports_latest and "
            f"were dropped from the search."
        )
    summary_lines.append("")
    summary_lines.append(
        "USER DISPLAY: the matched IDs render as a search in the viewer "
        "iframe below. The dropped IDs are listed in `unmatched_sample` "
        "above (up to 50 shown); summarize them for the user if asked. "
        "DO NOT restate the matched rows."
    )
    summary_lines.append("")
    summary_lines.append(f"Internal search handle for chaining: {stored['id']}.")

    return CreateFromFileResponse(
        id=stored["id"],
        count=stored["count"],
        submitted_count=submitted,
        unmatched_sample=unmatched[:50],
        unmatched_total=len(unmatched),
        view_url=_view_url(search_id),
        summary="\n".join(summary_lines),
    )


# ---------------------------------------------------------------------------
# GET /api/searches/{id}
# ---------------------------------------------------------------------------


@router.get("/{search_id}", response_model=SearchMeta)
async def get_search_meta(
    search_id: str,
    user: User = Depends(get_current_user),
) -> SearchMeta:
    ds = await store.get_search(search_id, user.sub)
    if ds is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return SearchMeta(
        id=ds["id"],
        id_column=ds["id_column"],
        count=ds["count"],
        sql=ds["sql"],
        owner_sub=ds["owner_sub"],
        created_at=ds["created_at"],
        highlight_terms=ds.get("highlight_terms") or [],
        highlight_diagnosis=ds.get("highlight_diagnosis") or [],
        sql_explanation=ds.get("sql_explanation") or "",
        owui_chat_id=ds.get("owui_chat_id") or "",
    )


# ---------------------------------------------------------------------------
# DELETE /api/searches/{id}
# ---------------------------------------------------------------------------


@router.delete("/{search_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_search(
    search_id: str,
    user: User = Depends(get_current_user),
) -> Response:
    """Delete a search by id. Owner-scoped — a delete against a search
    you don't own returns 404 (same shape as GET, so we don't leak the
    existence of other users' rows)."""
    deleted = await store.delete_search(search_id, user.sub)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Sort / filter helpers for /rows
# ---------------------------------------------------------------------------


_SORTABLE_COLUMNS: frozenset[str] = frozenset(
    {
        "accession_number",
        "epic_mrn",
        "message_dt",
        "modality",
        "service_name",
        "sending_facility",
        "patient_age",
        "sex",
        "evidence",
    }
)


def _parse_sort(sort: str | None) -> tuple[str, str] | None:
    if not sort:
        return None
    parts = sort.split(":", 1)
    col = parts[0]
    direction = parts[1].lower() if len(parts) > 1 else "asc"
    if col not in _SORTABLE_COLUMNS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"sort column {col!r} not allowed; one of {sorted(_SORTABLE_COLUMNS)}",
        )
    if direction not in ("asc", "desc"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"sort direction {direction!r} must be 'asc' or 'desc'",
        )
    return col, direction.upper()


# Columns that accept a `.min` / `.max` suffix; everything else single-value.
_RANGE_FILTER_COLUMNS: frozenset[str] = frozenset({"patient_age", "message_dt"})
# Columns whose repeated `filter.<col>=…` query params collapse into IN (…).
_MULTI_VALUE_COLUMNS: frozenset[str] = frozenset({"sex", "modality"})


def _parse_filters(request: Request) -> list[tuple[str, list[str]]]:
    grouped: dict[str, list[str]] = {}
    for key, val in request.query_params.multi_items():
        if not key.startswith("filter."):
            continue
        spec = key[len("filter.") :]
        col, _, suffix = spec.partition(".")
        if col not in _SORTABLE_COLUMNS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"filter column {col!r} not allowed; one of {sorted(_SORTABLE_COLUMNS)}",
            )
        if suffix:
            if col not in _RANGE_FILTER_COLUMNS or suffix not in ("min", "max"):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"filter suffix {suffix!r} not allowed on column "
                        f"{col!r} (range filters: {sorted(_RANGE_FILTER_COLUMNS)})"
                    ),
                )
        if val:
            grouped.setdefault(spec, []).append(val)
    return list(grouped.items())


def _filter_clause(col: str, values: list[str], *, alias: str = "") -> tuple[str, list]:
    """Return (sql_fragment, params) for one filter spec. Identifier
    is interpolated via _quote_ident; every value is bound via `?`."""
    prefix = f"{alias}." if alias else ""
    if "." in col:
        base, _, bound = col.partition(".")
        qcol = f"{prefix}{_quote_ident(base)}"
        value = values[0]
        if base == "patient_age":
            try:
                n = int(value)
            except ValueError:
                return "FALSE", []
            op = ">=" if bound == "min" else "<="
            return f"{qcol} {op} ?", [n]
        if base == "message_dt":
            try:
                date.fromisoformat(value)
            except ValueError:
                return "FALSE", []
            op = ">=" if bound == "min" else "<="
            return f"CAST({qcol} AS DATE) {op} CAST(? AS DATE)", [value]
        return "FALSE", []
    qcol = f"{prefix}{_quote_ident(col)}"
    if col in _MULTI_VALUE_COLUMNS:
        if not values:
            return "FALSE", []
        placeholders = ", ".join(["?"] * len(values))
        return f"{qcol} IN ({placeholders})", list(values)
    value = values[0]
    if col == "patient_age":
        try:
            n = int(value)
        except ValueError:
            return "FALSE", []
        return f"{qcol} = ?", [n]
    if col == "message_dt":
        return f"CAST({qcol} AS varchar) LIKE ?", ["%" + value + "%"]
    return f"LOWER({qcol}) LIKE LOWER(?)", ["%" + value + "%"]


# ---------------------------------------------------------------------------
# GET /api/searches/{id}/rows — wrap sql + paginate / sort / filter
# ---------------------------------------------------------------------------


@router.get("/{search_id}/rows", response_model=RowsResponse)
async def get_search_rows(
    search_id: str,
    request: Request,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=100, ge=1, le=1000),
    sort: str | None = Query(default=None, description="col:dir, e.g. message_dt:desc"),
    user: User = Depends(get_current_user),
) -> RowsResponse:
    """Paginated search rows. Wraps the saved sql as a
    subquery: `SELECT s.* FROM (<sql>) s [WHERE ...] [ORDER BY ...]
    OFFSET N LIMIT M`. Each page re-runs Trino — rows are never cached.

    Server-side filter values are ANDed and applied to whitelisted
    columns; sort accepts the same whitelist."""
    ds = await store.get_search(search_id, owner_sub=user.sub)
    if ds is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    cached_total = ds["count"]
    source_sql = ds["sql"]

    offset = (page - 1) * limit
    sort_spec = _parse_sort(sort)
    filters = _parse_filters(request)

    where_parts: list[str] = []
    filter_params: list = []
    for fcol, fvals in filters:
        clause, p = _filter_clause(fcol, fvals, alias="s")
        where_parts.append(clause)
        filter_params.extend(p)
    where_sql = (" WHERE " + " AND ".join(where_parts)) if where_parts else ""
    order_sql = ""
    if sort_spec:
        scol, sdir = sort_spec
        order_sql = f" ORDER BY s.{_quote_ident(scol)} {sdir} NULLS LAST"

    rows_sql = (
        f"SELECT s.* FROM ({source_sql}) s"
        f"{where_sql}"
        f"{order_sql} "
        f"OFFSET {offset} LIMIT {limit}"
    )

    # Total count after filtering. When no filter is active and we
    # have a cached_total, skip this query — saves a Trino scan per
    # page request. Sort-only doesn't change the count.
    if filters:
        count_sql = f"SELECT COUNT(*) AS n FROM ({source_sql}) s{where_sql}"
        try:
            with metrics.time_trino("rows_count_query"):
                _, count_rows = await trino_client.execute(
                    count_sql, user=user.sub, params=filter_params or None
                )
            sql_total = int(count_rows[0]["n"]) if count_rows else 0
        except Exception as exc:
            log.exception("trino count query failed")
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"trino count query failed: {exc}",
            )
    else:
        sql_total = cached_total

    try:
        with metrics.time_trino("rows_query"):
            columns, rows = await trino_client.execute(
                rows_sql, user=user.sub, params=filter_params or None
            )
    except Exception as exc:
        log.exception("trino rows query failed")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"trino rows query failed: {exc}",
        )

    ordered = json.loads(json.dumps(rows, default=str))
    return RowsResponse(
        id=search_id,
        page=page,
        limit=limit,
        total=sql_total,
        columns=columns,
        rows=ordered,
    )


# ---------------------------------------------------------------------------
# GET /api/searches/{id}/accessions
# ---------------------------------------------------------------------------


@router.get("/{search_id}/accessions")
async def get_search_accessions(
    search_id: str,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    ds = await store.get_search(search_id, owner_sub=user.sub)
    if ds is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    sql = ds["sql"]
    sql = (
        f"SELECT DISTINCT s.accession_number "
        f"FROM ({sql}) s "
        f"WHERE s.accession_number IS NOT NULL "
        f"ORDER BY s.accession_number"
    )
    try:
        with metrics.time_trino("accessions_query"):
            _cols, rows = await trino_client.execute(sql, user=user.sub)
    except Exception as exc:
        log.exception("trino accessions query failed")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"trino accessions query failed: {exc}",
        )
    return {
        "search_id": search_id,
        "accessions": [
            r["accession_number"] for r in rows if r.get("accession_number")
        ],
    }


# ---------------------------------------------------------------------------
# GET /api/searches/{id}/csv — streaming CSV, page-through via OFFSET/LIMIT
# ---------------------------------------------------------------------------


_CSV_COLUMNS = (
    "message_control_id",
    "accession_number",
    "modality",
    "service_name",
    "message_dt",
    "patient_age",
    "sex",
)
_CSV_CHUNK = 500


def _csv_quote(value: Any) -> str:
    if value is None:
        return ""
    s = str(value)
    if any(c in s for c in (",", '"', "\n", "\r")):
        return '"' + s.replace('"', '""') + '"'
    return s


@router.get("/{search_id}/csv")
async def export_search_csv(
    search_id: str,
    user: User = Depends(get_current_user),
) -> StreamingResponse:
    ds = await store.get_search(search_id, owner_sub=user.sub)
    if ds is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    sql = ds["sql"]
    total = ds["count"]
    cols_select = ", ".join(f"s.{_quote_ident(c)}" for c in _CSV_COLUMNS)

    async def gen():
        yield (",".join(_CSV_COLUMNS) + "\n").encode()
        if total == 0:
            return
        for offset in range(0, total, _CSV_CHUNK):
            sql = (
                f"SELECT {cols_select} FROM ({sql}) s "
                f"OFFSET {offset} LIMIT {_CSV_CHUNK}"
            )
            try:
                with metrics.time_trino("export_csv_query"):
                    _, rows = await trino_client.execute(sql, user=user.sub)
            except Exception:
                log.exception("trino export query failed at offset=%d", offset)
                yield b"# ERROR: query failed mid-export; file is incomplete\n"
                return
            for row in rows:
                yield (
                    ",".join(_csv_quote(row.get(c)) for c in _CSV_COLUMNS) + "\n"
                ).encode()

    return StreamingResponse(
        gen(),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{search_id}.csv"',
            "Cache-Control": "no-store",
        },
    )


# ---------------------------------------------------------------------------
# Snippet + summary helpers
# ---------------------------------------------------------------------------


def _extract_snippet(
    row: dict[str, Any], terms: list[str], *, window: int = 80
) -> str | None:
    """Return a short text excerpt around the first highlight_term hit
    in this row's report text. Pure regex; no stored snippets."""
    if not terms:
        return None
    escaped = [re.escape(t.strip()) for t in terms if t and t.strip()]
    if not escaped:
        return None
    pat = re.compile(r"(?is)\b(" + "|".join(escaped) + r")\b")
    for col in ("report_section_impression", "report_section_findings", "report_text"):
        text = row.get(col)
        if not text or not isinstance(text, str):
            continue
        m = pat.search(text)
        if not m:
            continue
        start = max(0, m.start() - window)
        end = min(len(text), m.end() + window)
        out = text[start:end].replace("\n", " ").strip()
        if start > 0:
            out = "…" + out
        if end < len(text):
            out = out + "…"
        return out
    return None


def _build_summary(
    *,
    total_rows: int,
    columns: list[str],
    sample_rows: list[dict[str, Any]],
    saved_search_id: str,
) -> str:
    """LLM-bound markdown summary returned alongside the search."""
    parts: list[str] = []
    rows_word = "row" if total_rows == 1 else "rows"
    parts.append(
        f"SQL matched {total_rows:,} {rows_word} across {len(columns)} columns."
    )
    parts.append("")
    parts.append(f"Columns: {', '.join(columns)}")
    if sample_rows:
        parts.append("")
        parts.append(
            "USER DISPLAY: an interactive table of these rows is rendered "
            "below your reply (sortable columns, header filters, "
            "click-row-to-expand for full report with matched terms "
            "highlighted, plus Export CSV and Send to XNAT). "
            "DO NOT restate the table or re-list rows in markdown — the "
            "user already sees them. Spend your reply on what the table "
            "can't carry: pattern observations, refinement suggestions, "
            "follow-up queries worth running, a one-sentence summary. "
            "The sample below is FOR YOUR REASONING ONLY; do not echo "
            "it back."
        )
        parts.append("")
        parts.append(
            f"Sample for your reasoning ({len(sample_rows)} of {total_rows:,} rows):"
        )
        visible_cols = [c for c in columns if c in sample_rows[0]]
        if visible_cols:
            header = "| " + " | ".join(visible_cols) + " |"
            sep = "|" + "|".join("---" for _ in visible_cols) + "|"
            parts.append(header)
            parts.append(sep)
            for r in sample_rows:
                cells = []
                for c in visible_cols:
                    v = str(r.get(c) or "")
                    v = v.replace("|", "\\|").replace("\n", " ")
                    if len(v) > 140:
                        v = v[:137] + "…"
                    cells.append(v)
                parts.append("| " + " | ".join(cells) + " |")
        # Snippet + positive_dx feedback lives on the sample rows
        # themselves (attached by the create flow) — surface them
        # alongside the table so the LLM sees per-row evidence.
        snippet_lines = []
        for i, r in enumerate(sample_rows):
            evidence = []
            if r.get("snippet"):
                evidence.append(f"snippet: \"{r['snippet']}\"")
            if r.get("positive_dx"):
                dx = "; ".join(f"{d['code']} ({d['text']})" for d in r["positive_dx"])
                evidence.append(f"matching dx: {dx}")
            if evidence:
                snippet_lines.append(f"  row {i+1}: " + " | ".join(evidence))
        if snippet_lines:
            parts.append("")
            parts.append("Evidence for each sample row:")
            parts.extend(snippet_lines)
    parts.append("")
    parts.append(
        f"Internal search handle: {saved_search_id}. Keep this backstage; "
        f"only mention it to the user when discussing the search by name. "
        f"XNAT export is a button in the viewer — only mention it when "
        f"the user explicitly says they're ready to export."
    )
    return "\n".join(parts)
