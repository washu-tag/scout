"""HTTP routes for `/api/searches`.

A search is a saved SQL query plus minimal metadata. Nothing about
which rows match is stored. Every read wraps `sql` and returns the full
cohort; sort/filter/paginate happen client-side in the SPA.

Endpoints:
  POST /api/searches                            - save SQL, return sample + count
  POST /api/searches/from-file                  - upload CSV of IDs, save contains(?, col) SQL and bind the ID list on every read
  GET  /api/searches/{id}                       - metadata
  GET  /api/searches/{id}/rows                  - full cohort (lean cols) for client-side browsing
  GET  /api/searches/{id}/accessions            - DISTINCT accession_number list

Single-report reads go through POST /api/reports/read (see routes/reports.py).
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Response,
    UploadFile,
    status,
)

from .. import metrics, trino_client
from ..store import SearchStore, get_store
from ..auth import User, get_current_user
from ..config import settings
from ..csv_upload import (
    DEFAULT_FROM_FILE_TABLE,
    UNMATCHED_SAMPLE_CAP,
    assert_cohort_placeholder,
    dedup_ids,
    guard_upload_size,
    parse_csv_ids,
    substitute_cohort,
)
from ..ids import new_search_id
from ..logging_setup import scrub_for_log
from ..models import (
    SEARCH_REQUIRED_COLUMNS,
    CreateFromFileResponse,
    CreateSearchRequest,
    CreateSearchResponse,
    ExportToSupersetResponse,
    RowsResponse,
    SearchMeta,
)

# PoC only (#628) - must match CHAT_COHORT_EXPORT_TOKEN in the Superset
# KeycloakSecurityManager config override
# (ansible/roles/superset/templates/values.yaml.j2). Authenticates only
# calls to Superset's /internal/chat-cohort-export endpoint. Replace with a
# provisioned secret before this leaves prototype status.
_CHAT_COHORT_EXPORT_TOKEN = "scout-poc-chat-cohort-export-8f2e1c"

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/searches", tags=["searches"])


_LLM_SAMPLE_ROWS = 10

# Report-body columns never sent to the grid: too large for 50k-row payloads,
# and the SPA fetches them per-row via /reports/read on expand. Shared by the
# create-sample response and the full-cohort fetch.
_HEAVY_COLS: frozenset[str] = frozenset(
    {
        "report_text",
        "report_section_findings",
        "report_section_impression",
        "report_section_addendum",
    }
)


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
    return f"{settings.trino_catalog}.{settings.trino_schema}.reports_curated"


def _view_url(search_id: str) -> str:
    return f"{settings.external_url.rstrip('/')}/spa/searches/{search_id}"


def _wrap_sql(sql: str) -> str:
    """Strip trailing semicolons so the SQL can be nested as a subquery."""
    return sql.rstrip().rstrip(";")


def _meta_from_row(r: dict[str, Any]) -> SearchMeta:
    return SearchMeta(
        id=r["id"],
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
    additional small query against reports_curated to populate per-row
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
    except Exception:
        log.exception("trino count query failed")
        # NULL (unknown), not 0, so reads can tell a failed count from empty.
        row_count = None

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

    _drop_cols = _HEAVY_COLS
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
        sql=sql,
        owner_sub=user.sub,
        match_terms=body.match_terms or [],
        match_diagnoses=body.match_diagnoses or [],
        sql_explanation=body.sql_explanation or "",
        owui_chat_id=body.owui_chat_id or "",
    )

    metrics.SEARCHES_CREATED.inc()
    if row_count is not None:
        metrics.SEARCH_SIZE.observe(row_count)
    log.info(
        "search created",
        extra={
            "search_id": stored["id"],
            "count": row_count,
            "id_column": id_column,
            "user_sub": user.sub,
        },
    )

    return CreateSearchResponse(
        id=stored["id"],
        count=row_count,
        id_column=id_column,
        view_url=_view_url(search_id),
        columns=[c for c in columns if c not in _drop_cols],
        sample=sample,
        evidence=evidence,
    )


# Default projection when a CSV upload has no custom SQL.
_DEFAULT_FROM_FILE_SQL = (
    "SELECT primary_report_identifier, accession_number, epic_mrn, patient_mpi, "
    "sending_facility, modality, service_name, "
    "message_dt, patient_age, sex "
    "FROM reports_latest "
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
    default projection over reports_latest is used (consistent with the
    chat cohort default), matching the raw id columns. A user wanting report
    history or resolved cross-version patient IDs passes explicit SQL over
    reports_curated / an epic view."""
    try:
        raw = await file.read()
    finally:
        await file.close()
    guard_upload_size(raw)
    if sql:
        assert_cohort_placeholder(sql)
    ids, resolved_id_column, column_inferred = parse_csv_ids(raw, id_column)
    cleaned = dedup_ids(ids)

    col_q = _quote_ident(resolved_id_column)

    # All IDs are bound at read time; validate only on the default path, where
    # the table is known, to report unmatched. Custom SQL targets an unknown
    # table, so skip validation rather than check the wrong one.
    unmatched: list[str] = []
    if not sql:
        view = f"{settings.trino_catalog}.{settings.trino_schema}.{DEFAULT_FROM_FILE_TABLE}"
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
        unmatched = [i for i in cleaned if i not in matched]
        if len(unmatched) == len(cleaned):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"none of the {len(cleaned)} submitted IDs matched "
                    f"{resolved_id_column} in {DEFAULT_FROM_FILE_TABLE}"
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
                sample_sql, user=user.sub, params=[cleaned]
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
                count_sql, user=user.sub, params=[cleaned]
            )
        row_count = int(count_rows[0]["n"]) if count_rows else 0
    except Exception:
        log.exception("trino from-file count failed")
        row_count = None

    search_id = new_search_id()
    stored = await store.insert_search(
        search_id=search_id,
        sql=saved_sql,
        owner_sub=user.sub,
        uploaded_ids=cleaned,
        sql_explanation=sql_explanation or "",
        owui_chat_id=owui_chat_id or "",
    )

    metrics.SEARCHES_CREATED.inc()
    if row_count is not None:
        metrics.SEARCH_SIZE.observe(row_count)
    log.info(
        "search imported from file",
        extra={
            "search_id": stored["id"],
            "id_column": scrub_for_log(resolved_id_column),
            "column_inferred": column_inferred,
            "unique_ids": len(cleaned),
            # custom SQL targets an unknown table, so nothing is validated
            "matched_ids": (len(cleaned) - len(unmatched)) if not sql else None,
            "unmatched_ids": len(unmatched) if not sql else None,
            "report_count": row_count,
            "custom_sql": bool(sql),
        },
    )

    return CreateFromFileResponse(
        id=stored["id"],
        id_column=resolved_id_column,
        column_inferred=column_inferred,
        count=row_count,
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


def _inline_uploaded_ids(sql: str, uploaded_ids: list[str] | None) -> str:
    """Superset's virtual dataset runs `sql` with no param binding, so a
    from-file search's single `contains(?, col)` predicate needs its ids
    inlined as a literal array before export."""
    if not uploaded_ids:
        return sql
    escaped = ",".join("'" + v.replace("'", "''") + "'" for v in uploaded_ids)
    return sql.replace("?", f"ARRAY[{escaped}]", 1)


@router.post(
    "/{search_id}/export-to-superset",
    response_model=ExportToSupersetResponse,
)
async def export_search_to_superset(
    search_id: str,
    user: User = Depends(get_current_user),
    store: SearchStore = Depends(get_store),
) -> ExportToSupersetResponse:
    """Issue #628 PoC: create a Superset dataset scoped to this cohort's SQL.

    The dataset is tagged with a schema Gamma has no blanket schema_access
    to, so it isn't visible to other users - Superset's KeycloakSecurityManager
    (ansible/roles/superset/templates/values.yaml.j2) grants access back only
    to the caller via a chat_cohort_grants row the Superset endpoint inserts.
    """
    ds = await store.get_search(search_id, owner_sub=user.sub)
    if ds is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    sql = _inline_uploaded_ids(ds["sql"], ds.get("uploaded_ids"))
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{settings.superset_internal_url}/internal/chat-cohort-export",
                json={
                    "sql": sql,
                    "username": user.sub,
                    "title": ds.get("sql_explanation") or "Chat cohort export",
                },
                headers={"X-Cohort-Export-Token": _CHAT_COHORT_EXPORT_TOKEN},
            )
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        log.exception("superset export failed")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"superset export failed: {exc}",
        )
    payload = resp.json()
    return ExportToSupersetResponse(
        dataset_id=payload["dataset_id"],
        explore_url=f"{settings.external_url.rstrip('/')}/superset{payload['explore_url']}",
    )


def _rows_query_error(exc: Exception, stage: str) -> HTTPException:
    log.exception("trino %s query failed", stage)
    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail=f"trino {stage} query failed: {exc}",
    )


@router.get("/{search_id}/rows", response_model=RowsResponse)
async def get_search_rows(
    search_id: str,
    user: User = Depends(get_current_user),
    store: SearchStore = Depends(get_store),
) -> RowsResponse:
    """The full cohort in one response, for client-side browsing.

    Runs the saved sql once and returns every matched row (up to
    settings.max_cohort_rows), with report-body columns stripped - the SPA
    holds the set in memory and does sort/filter/paginate client-side, and
    fetches report text per-row via /reports/read on expand. A cohort larger
    than the cap is truncated with `truncated=true`. One Trino scan; no
    sort/filter/pagination params."""
    ds = await store.get_search(search_id, owner_sub=user.sub)
    if ds is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    source_sql = ds["sql"]
    uploaded_ids = ds.get("uploaded_ids")
    cap = settings.max_cohort_rows
    # Fetch cap+1 so we can flag truncation without a separate COUNT.
    all_sql = f"SELECT s.* FROM ({source_sql}) s LIMIT {cap + 1}"
    try:
        with metrics.time_trino("rows_query"):
            # safe: source_sql is persisted validated SQL; ids bind via ?
            # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
            columns, rows = await trino_client.execute(
                all_sql,
                user=user.sub,
                params=[uploaded_ids] if uploaded_ids else None,
            )
    except Exception as exc:
        raise _rows_query_error(exc, "rows")

    truncated = len(rows) > cap
    if truncated:
        rows = rows[:cap]
    metrics.RESULT_ROWS.labels(op="rows_query").observe(len(rows))
    lean_columns = [c for c in columns if c not in _HEAVY_COLS]
    lean_rows = [{k: v for k, v in r.items() if k not in _HEAVY_COLS} for r in rows]
    return RowsResponse(
        id=search_id,
        columns=lean_columns,
        rows=lean_rows,
        total=len(lean_rows),
        truncated=truncated,
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
