"""HTTP routes for `/api/searches`.

A search is a saved SQL query plus minimal metadata. Nothing about
which rows match is stored. Every read wraps `sql` as a
subquery and applies pagination/sort/filter at the Trino layer.

Endpoints:
  POST /api/searches                            - save SQL, cache COUNT(*), return sample
  POST /api/searches/from-file                  - upload CSV of IDs, save contains(?, col) SQL and bind the ID list on every read
  GET  /api/searches/{id}                       - metadata
  GET  /api/searches/{id}/rows                  - paginated rows (wraps sql)
  GET  /api/searches/{id}/accessions            - DISTINCT accession_number list
  GET  /api/searches/{id}/csv                   - streaming CSV download

Single-report reads go through POST /api/reports/read (see routes/reports.py).
"""

from __future__ import annotations

import logging
import re
from datetime import date
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import StreamingResponse

from .. import metrics, trino_client
from ..store import SearchStore, get_store
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
from ..ids import new_search_id
from ..logging_setup import scrub_for_log
from ..models import (
    SEARCH_REQUIRED_COLUMNS,
    CreateFromFileResponse,
    CreateSearchRequest,
    CreateSearchResponse,
    RowsResponse,
    SearchMeta,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/searches", tags=["searches"])


_LLM_SAMPLE_ROWS = 10


def _assert_required_projections(columns: list[str]) -> None:
    missing = [c for c in SEARCH_REQUIRED_COLUMNS if c not in columns]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"SELECT must project {list(SEARCH_REQUIRED_COLUMNS)}; "
                f"missing: {missing}. Got columns: {columns}"
            ),
        )


# Identifiers can't be param-bound in Trino; values always are.
def _quote_ident(name: str) -> str:
    if not name.replace("_", "").isalnum():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unsafe identifier: {name!r}",
        )
    return f'"{name}"'


def _qualified_reports() -> str:
    return f"{settings.trino_catalog}.{settings.trino_schema}.reports_latest"


def _qualified_reports_epic_view() -> str:
    return f"{settings.trino_catalog}.{settings.trino_schema}.reports_latest_epic_view"


def _view_url(search_id: str) -> str:
    return f"{settings.external_url.rstrip('/')}/spa/searches/{search_id}"


def _wrap_sql(sql: str) -> str:
    """Strip trailing semicolons so the SQL can be nested as a subquery."""
    return sql.rstrip().rstrip(";")


def _meta_from_row(r: dict[str, Any]) -> SearchMeta:
    return SearchMeta(
        id=r["id"],
        id_column=r["id_column"],
        count=r["count"],
        sql=r["sql"],
        owner_sub=r["owner_sub"],
        created_at=r["created_at"],
        match_terms=r.get("match_terms") or [],
        match_diagnoses=r.get("match_diagnoses") or [],
        sql_explanation=r.get("sql_explanation") or "",
        owui_chat_id=r.get("owui_chat_id") or "",
    )


@router.get("", response_model=list[SearchMeta])
async def list_searches(
    user: User = Depends(get_current_user),
    store: SearchStore = Depends(get_store),
) -> list[SearchMeta]:
    """Caller's searches, newest first. Drives the SPA homepage.
    Owner-scoped - only the authenticated user's own."""
    rows = await store.list_searches(user.sub)
    return [_meta_from_row(r) for r in rows]


@router.post(
    "",
    response_model=CreateSearchResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_search(
    body: CreateSearchRequest,
    user: User = Depends(get_current_user),
    store: SearchStore = Depends(get_store),
) -> CreateSearchResponse:
    """Save a SQL query as a search. No row materialization - runs one
    `SELECT COUNT(*)` to cache the count, fetches a small sample for
    the LLM, and (if match_terms or match_diagnoses is set) one
    additional small query against reports_latest to populate per-row
    evidence (excerpt + matched_diagnoses).

    Refinement: when the LLM wants to narrow a search, it writes a new
    `POST /searches` call with the original conditions plus the new
    constraint. The saved SQL is standalone, no placeholder
    substitution, no parent reference required.
    """
    sql = _wrap_sql(body.sql)

    # Sample query doubles as SQL validation: errors surface here before
    # we persist anything. The LIMIT lives outside the saved sql so the
    # LLM's own LIMIT is respected on later /rows reads.
    sample_sql = f"SELECT s.* FROM ({sql}) s LIMIT {_LLM_SAMPLE_ROWS}"
    try:
        with metrics.time_trino("create_sample_query"):
            # safe: LLM-authored SQL wrapped as subquery, OPA is the AuthZ boundary
            # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
            columns, sample_rows = await trino_client.execute(sample_sql, user=user.sub)
    except Exception as exc:
        log.exception("trino sample query failed")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"trino query failed: {exc}",
        )
    _assert_required_projections(columns)
    id_column = "primary_report_identifier"

    count_sql = f"SELECT COUNT(*) AS n FROM ({sql}) s"
    try:
        with metrics.time_trino("create_count_query"):
            # safe: LLM-authored SQL wrapped as subquery, OPA is the AuthZ boundary
            # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
            _cols, count_rows = await trino_client.execute(count_sql, user=user.sub)
        row_count = int(count_rows[0]["n"]) if count_rows else 0
    except Exception as exc:
        log.exception("trino count query failed")
        # Non-fatal: search still gets created, just without cached
        # count. Reads will report 0 until the next refresh, which is
        # better than failing the whole create.
        row_count = 0

    sample_extras: dict[str, dict[str, Any]] = {}
    if body.match_terms or body.match_diagnoses:
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
                # Evidence is a nice-to-have; carry on without it.
                log.exception("sample-text fetch failed (non-fatal)")

    # \b boundaries so short tokens like "PE" don't match in "pectoralis".
    match_pattern = None
    if body.match_terms:
        atoms = [re.escape(t.strip()) for t in body.match_terms if t and t.strip()]
        if atoms:
            match_pattern = re.compile(r"(?is)\b(" + "|".join(atoms) + r")\b")

    # Strip SQL-LIKE `%` so the LLM can pass `R91` or `R91%` - same thing.
    dx_prefixes: list[str] = []
    if body.match_diagnoses:
        for d in body.match_diagnoses:
            if d and d.strip():
                dx_prefixes.append(d.strip().rstrip("%").lower())

    _drop_cols = {
        "report_text",
        "report_section_findings",
        "report_section_impression",
        "report_section_addendum",
    }
    sample: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    for r in sample_rows:
        row_out = {k: v for k, v in r.items() if k not in _drop_cols}
        ev: dict[str, Any] = {
            id_column: r.get(id_column),
            "excerpt": None,
            "matched_diagnoses": [],
        }
        if body.match_terms or dx_prefixes:
            key = str(r.get(id_column)) if r.get(id_column) is not None else None
            extra = sample_extras.get(key, {}) if key else {}
            merged = {**r, **extra}
            if body.match_terms:
                ev["excerpt"] = _extract_excerpt(merged, body.match_terms)
            dxs = extra.get("diagnoses") or r.get("diagnoses") or []
            matched_diagnoses: list[dict[str, str]] = []
            for d in dxs if isinstance(dxs, list) else []:
                if not isinstance(d, dict):
                    continue
                code = str(d.get("diagnosis_code") or "")
                text = str(d.get("diagnosis_code_text") or "")
                if not code:
                    continue
                code_lc = code.lower()
                if dx_prefixes and any(code_lc.startswith(p) for p in dx_prefixes):
                    matched_diagnoses.append({"code": code, "text": text})
                elif match_pattern and match_pattern.search(f"{code} {text}"):
                    matched_diagnoses.append({"code": code, "text": text})
            ev["matched_diagnoses"] = matched_diagnoses
        sample.append(row_out)
        evidence.append(ev)

    search_id = new_search_id()
    stored = await store.insert_search(
        search_id=search_id,
        id_column=id_column,
        sql=sql,
        owner_sub=user.sub,
        row_count=row_count,
        match_terms=body.match_terms or [],
        match_diagnoses=body.match_diagnoses or [],
        sql_explanation=body.sql_explanation or "",
        owui_chat_id=body.owui_chat_id or "",
    )

    metrics.SEARCHES_CREATED.inc()
    metrics.SEARCH_SIZE.observe(stored["count"])
    log.info(
        "search created",
        extra={
            "search_id": stored["id"],
            "count": stored["count"],
            "id_column": id_column,
            "user_sub": user.sub,
        },
    )

    return CreateSearchResponse(
        id=stored["id"],
        count=stored["count"],
        id_column=stored["id_column"],
        view_url=_view_url(search_id),
        columns=[c for c in columns if c not in _drop_cols],
        sample=sample,
        evidence=evidence,
    )


_DEFAULT_FROM_FILE_SQL = (
    "SELECT primary_report_identifier, accession_number, "
    "resolved_epic_mrn AS epic_mrn, resolved_mpi AS mpi, "
    "sending_facility, modality, service_name, "
    "message_dt, patient_age, sex "
    "FROM reports_latest_epic_view "
    "WHERE {{cohort}}"
)


@router.post(
    "/from-file",
    response_model=CreateFromFileResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_search_from_file(
    file: UploadFile = File(...),
    id_column: str | None = Form(default=None),
    sql: str | None = Form(default=None),
    sql_explanation: str | None = Form(default=None),
    owui_chat_id: str | None = Form(default=None),
    user: User = Depends(get_current_user),
    store: SearchStore = Depends(get_store),
) -> CreateFromFileResponse:
    """Materialize a search from a researcher-supplied CSV of IDs.

    The uploaded CSV must have a header row. `id_column` is either sent
    explicitly (one of FILE_UPLOAD_ID_COLUMNS) or inferred from the
    header via FILE_UPLOAD_HEADER_ALIASES.

    If `sql` is provided it must include `{{cohort}}` exactly once; the
    backend substitutes a `contains(?, col)` predicate and stores the ID
    list separately so every read binds it as a param. When omitted, a
    default projection over reports_latest_epic_view is used.

    Patient-scoped inputs are translated through PATIENT_ID_COLUMNS so
    `epic_mrn` filters on `resolved_epic_mrn`."""
    try:
        raw = await file.read()
    finally:
        await file.close()
    guard_upload_size(raw)
    if sql:
        assert_cohort_placeholder(sql)
    ids, resolved_id_column, column_inferred = parse_csv_ids(raw, id_column)
    resolved_id_column = scrub_for_log(resolved_id_column)
    cleaned = dedup_ids(ids)

    sql_column = resolve_sql_column(resolved_id_column)
    view = _qualified_reports_epic_view()
    col_q = _quote_ident(sql_column)

    matched: set[str] = set()
    CHUNK = 5000
    validate_sql = (
        f"SELECT DISTINCT {col_q} AS id FROM {view} WHERE contains(?, {col_q})"
    )
    for start in range(0, len(cleaned), CHUNK):
        chunk = cleaned[start : start + CHUNK]
        try:
            with metrics.time_trino("from_file_validate"):
                # safe: identifier from _quote_ident allowlist, IDs bind via ?
                # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
                _cols, rows = await trino_client.execute(
                    validate_sql, user=user.sub, params=[chunk]
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
                f"{resolved_id_column} in reports_latest_epic_view"
            ),
        )

    predicate = f"contains(?, {col_q})"
    template = _wrap_sql(sql) if sql else _DEFAULT_FROM_FILE_SQL
    saved_sql = substitute_cohort(template, predicate)

    sample_sql = f"SELECT s.* FROM ({saved_sql}) s LIMIT {_LLM_SAMPLE_ROWS}"
    try:
        with metrics.time_trino("from_file_sample"):
            # safe: saved_sql uses contains(?, col); IDs bind at execute
            # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
            columns, sample_rows = await trino_client.execute(
                sample_sql, user=user.sub, params=[final_ids]
            )
    except Exception as exc:
        log.exception("trino from-file sample failed")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"trino query failed: {exc}. Your SQL (before "
                f"{{{{cohort}}}} substitution): {sql or '<default template>'}"
            ),
        )
    if sql:
        _assert_required_projections(columns)

    count_sql = f"SELECT COUNT(*) AS n FROM ({saved_sql}) s"
    try:
        with metrics.time_trino("from_file_count"):
            # safe: saved_sql uses contains(?, col); IDs bind at execute
            # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
            _cols, count_rows = await trino_client.execute(
                count_sql, user=user.sub, params=[final_ids]
            )
        row_count = int(count_rows[0]["n"]) if count_rows else 0
    except Exception:
        log.exception("trino from-file count failed")
        row_count = 0

    search_id = new_search_id()
    stored = await store.insert_search(
        search_id=search_id,
        id_column=resolved_id_column,
        sql=saved_sql,
        owner_sub=user.sub,
        row_count=row_count,
        uploaded_ids=final_ids,
        sql_explanation=sql_explanation or "",
        owui_chat_id=owui_chat_id or "",
    )

    metrics.SEARCHES_CREATED.inc()
    metrics.SEARCH_SIZE.observe(stored["count"])
    log.info(
        "search imported from file",
        extra={
            "search_id": stored["id"],
            "id_column": resolved_id_column,
            "column_inferred": column_inferred,
            "unique_ids": len(cleaned),
            "matched_ids": len(final_ids),
            "unmatched_ids": len(unmatched),
            "report_count": row_count,
            "custom_sql": bool(sql),
        },
    )

    return CreateFromFileResponse(
        id=stored["id"],
        id_column=stored["id_column"],
        column_inferred=column_inferred,
        count=stored["count"],
        columns=columns,
        sample=sample_rows,
        unmatched=unmatched[:UNMATCHED_SAMPLE_CAP],
        unmatched_count=len(unmatched),
        view_url=_view_url(search_id),
    )


@router.get("/{search_id}", response_model=SearchMeta)
async def get_search_meta(
    search_id: str,
    user: User = Depends(get_current_user),
    store: SearchStore = Depends(get_store),
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
        match_terms=ds.get("match_terms") or [],
        match_diagnoses=ds.get("match_diagnoses") or [],
        sql_explanation=ds.get("sql_explanation") or "",
        owui_chat_id=ds.get("owui_chat_id") or "",
    )


@router.delete("/{search_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_search(
    search_id: str,
    user: User = Depends(get_current_user),
    store: SearchStore = Depends(get_store),
) -> Response:
    """Delete a search by id. Owner-scoped - a delete against a search
    you don't own returns 404 (same shape as GET, so we don't leak the
    existence of other users' rows)."""
    deleted = await store.delete_search(search_id, user.sub)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


_SORTABLE_COLUMNS: frozenset[str] = frozenset(
    {
        "accession_number",
        "epic_mrn",
        "mpi",
        "message_dt",
        "modality",
        "service_name",
        "sending_facility",
        "patient_age",
        "sex",
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


def _like_escape(value: str) -> str:
    return value.replace("!", "!!").replace("%", "!%").replace("_", "!_")


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
    pattern = "%" + _like_escape(value) + "%"
    if col == "message_dt":
        return f"CAST({qcol} AS varchar) LIKE ? ESCAPE '!'", [pattern]
    return f"LOWER({qcol}) LIKE LOWER(?) ESCAPE '!'", [pattern]


@router.get("/{search_id}/rows", response_model=RowsResponse)
async def get_search_rows(
    search_id: str,
    request: Request,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=100, ge=1, le=1000),
    sort: str | None = Query(default=None, description="col:dir, e.g. message_dt:desc"),
    user: User = Depends(get_current_user),
    store: SearchStore = Depends(get_store),
) -> RowsResponse:
    """Paginated search rows. Wraps the saved sql as a
    subquery: `SELECT s.* FROM (<sql>) s [WHERE ...] [ORDER BY ...]
    OFFSET N LIMIT M`. Each page re-runs Trino - rows are never cached.

    Server-side filter values are ANDed and applied to whitelisted
    columns; sort accepts the same whitelist."""
    ds = await store.get_search(search_id, owner_sub=user.sub)
    if ds is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    cached_total = ds["count"]
    source_sql = ds["sql"]
    uploaded_ids = ds.get("uploaded_ids")
    base_params: list = [uploaded_ids] if uploaded_ids else []

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
    if sort_spec:
        scol, sdir = sort_spec
        order_sql = f" ORDER BY s.{_quote_ident(scol)} {sdir} NULLS LAST, s.primary_report_identifier"
    else:
        # Stable default so OFFSET/LIMIT doesn't drift across pages.
        order_sql = " ORDER BY s.primary_report_identifier"

    rows_sql = (
        f"SELECT s.* FROM ({source_sql}) s"
        f"{where_sql}"
        f"{order_sql} "
        f"OFFSET {offset} LIMIT {limit}"
    )

    # Total count after filtering. When no filter is active and we
    # have a cached_total, skip this query - saves a Trino scan per
    # page request. Sort-only doesn't change the count.
    if filters:
        count_sql = f"SELECT COUNT(*) AS n FROM ({source_sql}) s{where_sql}"
        try:
            with metrics.time_trino("rows_count_query"):
                # safe: source_sql is persisted validated SQL, filter values bind via ?
                # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
                _, count_rows = await trino_client.execute(
                    count_sql,
                    user=user.sub,
                    params=(base_params + filter_params) or None,
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
                rows_sql, user=user.sub, params=(base_params + filter_params) or None
            )
    except Exception as exc:
        log.exception("trino rows query failed")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"trino rows query failed: {exc}",
        )

    return RowsResponse(
        id=search_id,
        page=page,
        limit=limit,
        total=sql_total,
        columns=columns,
        rows=rows,
    )


@router.get("/{search_id}/accessions")
async def get_search_accessions(
    search_id: str,
    user: User = Depends(get_current_user),
    store: SearchStore = Depends(get_store),
) -> dict[str, Any]:
    ds = await store.get_search(search_id, owner_sub=user.sub)
    if ds is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    sql = ds["sql"]
    uploaded_ids = ds.get("uploaded_ids")
    sql = (
        f"SELECT DISTINCT s.accession_number "
        f"FROM ({sql}) s "
        f"WHERE s.accession_number IS NOT NULL "
        f"ORDER BY s.accession_number"
    )
    try:
        with metrics.time_trino("accessions_query"):
            _cols, rows = await trino_client.execute(
                sql, user=user.sub, params=[uploaded_ids] if uploaded_ids else None
            )
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


_CSV_CHUNK = 500


# Spreadsheet formula-injection prefixes.
_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@")


def _csv_quote(value: Any) -> str:
    if value is None:
        return ""
    s = str(value)
    if s and s[0] in _CSV_FORMULA_PREFIXES:
        s = "'" + s
    if any(c in s for c in (",", '"', "\n", "\r")):
        return '"' + s.replace('"', '""') + '"'
    return s


@router.get("/{search_id}/csv")
async def export_search_csv(
    search_id: str,
    user: User = Depends(get_current_user),
    store: SearchStore = Depends(get_store),
) -> StreamingResponse:
    ds = await store.get_search(search_id, owner_sub=user.sub)
    if ds is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    sql = ds["sql"]
    uploaded_ids = ds.get("uploaded_ids")

    async def gen():
        params = [uploaded_ids] if uploaded_ids else None
        export_sql = (
            f"SELECT s.* FROM ({sql}) s "
            f"ORDER BY s.{_quote_ident('primary_report_identifier')}"
        )
        header_written = False
        try:
            with metrics.time_trino("export_csv_query"):
                async for columns, rows in trino_client.stream(
                    export_sql, user=user.sub, params=params, chunk_size=_CSV_CHUNK
                ):
                    if not header_written:
                        yield (",".join(columns) + "\n").encode()
                        header_written = True
                    for row in rows:
                        yield (
                            ",".join(_csv_quote(row.get(c)) for c in columns) + "\n"
                        ).encode()
        except Exception:
            log.exception("trino export query failed")
            yield b"# ERROR: query failed mid-export; file is incomplete\n"
            return

    return StreamingResponse(
        gen(),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{search_id}.csv"',
            "Cache-Control": "no-store",
        },
    )


def _extract_excerpt(
    row: dict[str, Any], terms: list[str], *, window: int = 80
) -> str | None:
    """Excerpt of ±window chars around the first match_terms hit in
    this row's parsed report sections, falling back to report_text."""
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
