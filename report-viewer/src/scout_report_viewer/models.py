"""Pydantic request/response models for the public API."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# Identifier columns accepted as inputs to /reports/read and
# /searches/from-file.
INPUT_ID_COLUMNS: tuple[str, ...] = (
    "primary_report_identifier",
    "accession_number",
    "epic_mrn",
    "patient_mpi",
    "scout_patient_id",
)

# The columns an uploaded CSV can key on. Order is preference: inference picks
# the first whose header matches, so report-scoped (primary_report_identifier,
# accession) wins over patient-scoped (mrn/patient_mpi).
FILE_UPLOAD_ID_COLUMNS: tuple[str, ...] = (
    "primary_report_identifier",
    "accession_number",
    "epic_mrn",
    "patient_mpi",
)

# Header aliases for CSV column inference. Substring match on lowercased
# headers, first hit wins.
FILE_UPLOAD_HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "epic_mrn": ("epic_mrn", "epicmrn", "mrn", "patient_mrn", "patient_id"),
    "accession_number": ("accession_number", "accession", "acc_num"),
    "patient_mpi": ("patient_mpi", "mpi", "empi"),
}

# Every saved /searches SQL must project these in its outer SELECT.
SEARCH_REQUIRED_COLUMNS: tuple[str, ...] = (
    "primary_report_identifier",
    "accession_number",
)

# Tables /reports/read may target; renders default to reports_curated, epic
# views are opt-in for resolved cross-version patient identity.
READ_REPORTS_TABLES: tuple[str, ...] = (
    "reports_curated",
    "reports_curated_epic_view",
    "reports_latest",
    "reports_latest_epic_view",
)
DEFAULT_READ_REPORTS_TABLE = "reports_curated"

# The *_epic_view subset (carry resolved_* / scout_patient_id).
EPIC_VIEW_TABLES: frozenset[str] = frozenset(
    {"reports_curated_epic_view", "reports_latest_epic_view"}
)


class CreateSearchRequest(BaseModel):
    sql: str = Field(
        ...,
        description=(
            "Trino SQL to save. Outer SELECT must project "
            "primary_report_identifier and accession_number."
        ),
    )
    sql_explanation: str | None = Field(
        default=None,
        description=(
            "Plain-language summary of what the SQL matches and why. "
            "Surfaced in the SPA's 'About this search' panel so the "
            "user can sanity-check the search definition without "
            "reading raw SQL."
        ),
    )
    owui_chat_id: str | None = Field(
        default=None,
        description=(
            "The OWUI conversation ID this search was created from. "
            "The SPA homepage groups searches by chat so a user "
            "reviewing their work sees searches organized by the "
            "conversation that produced them."
        ),
    )


class CreateFromFileResponse(BaseModel):
    id: str
    id_column: str
    column_inferred: bool
    count: int | None
    columns: list[str]
    sample: list[dict[str, Any]]
    unmatched: list[str]
    unmatched_count: int
    view_url: str


class QueryRequest(BaseModel):
    """One-shot SQL query - runs against Trino, returns rows directly,
    persists nothing. Backs the `scout_query_sql` tool surface for
    aggregate / COUNT / GROUP BY questions where the user wants prose,
    not a search viewer."""

    sql: str = Field(..., description="Trino SQL to execute.")


class QueryResponse(BaseModel):
    columns: list[str]
    rows: list[dict[str, Any]]


class QueryFromFileResponse(BaseModel):
    columns: list[str]
    rows: list[dict[str, Any]]
    id_column: str
    column_inferred: bool


MAX_READ_REPORTS_IDS = 100


class ReadReportsRequest(BaseModel):
    """Fetch the full content of specific reports by ID. Backs the
    `scout_get_reports` tool surface AND the SPA row-expand panel
    (which sends an array of one)."""

    ids: list[str] = Field(
        ...,
        max_length=MAX_READ_REPORTS_IDS,
        description="Report identifiers to fetch.",
    )
    id_column: str = Field(
        default="primary_report_identifier",
        description="Column to match `ids` against.",
    )
    search_id: str | None = Field(
        default=None,
        description=(
            "Optional search to derive highlight offsets from. When set, the "
            "response carries a `highlights` entry per row."
        ),
    )
    table: str | None = Field(
        default=None,
        description=(
            "Table to read from. One of reports_curated (default), "
            "reports_curated_epic_view, reports_latest, "
            "reports_latest_epic_view."
        ),
    )


class Highlight(BaseModel):
    """One match of a search pattern, as offsets into the text in this response."""

    field: str
    start: int
    end: int
    polarity: str


class ReadReportsResponse(BaseModel):
    columns: list[str]
    rows: list[dict[str, Any]]
    # Parallel-indexed to `rows`; empty unless the request named a search.
    highlights: list[list[Highlight]] = []


class CreateSearchResponse(BaseModel):
    id: str
    id_column: str
    view_url: str
    columns: list[str]
    sample: list[dict[str, Any]]
    # Parallel-indexed to `sample`. Each item is {id_column: value,
    # matched_on, positive_evidence, negative_evidence, matched_diagnoses}.
    evidence: list[dict[str, Any]]


class SearchMeta(BaseModel):
    id: str
    sql: str
    # The sql as rewritten to project evidence. Empty when there is none.
    executed_sql: str = ""
    owner_sub: str
    created_at: datetime
    match_terms: list[str] = []
    match_diagnoses: list[str] = []
    # Plain-language summary of what the SQL matches and why,
    # written by the LLM at create time. Surfaced in the SPA's
    # "About this search" panel. Empty string if not provided.
    sql_explanation: str = ""
    # OWUI conversation ID - drives the SPA homepage's per-chat
    # grouping. Empty when the caller didn't supply it.
    owui_chat_id: str = ""


class RowsResponse(BaseModel):
    """The full cohort in one response for client-side sort/filter/paginate.
    Lean columns only (report bodies dropped; fetched per-row via
    /reports/read). `truncated` is true when the cohort exceeded the cap."""

    id: str
    columns: list[str]
    rows: list[dict[str, Any]]
    total: int
    truncated: bool


class PlotRequest(BaseModel):
    """SQL plus the LLM's Vega-Lite spec, minus its data. Backs
    `scout_chart_sql`."""

    sql: str
    vega_lite_spec: dict[str, Any]
    sql_explanation: str = ""
    owui_chat_id: str = ""


class PlotResponse(BaseModel):
    """Where the chart can be viewed. No spec, no rows: they stay server-side."""

    id: str
    view_url: str
    columns: list[str]


class PlotMeta(BaseModel):
    """One saved chart as it appears in the SPA's listing. No spec and no
    rows - the listing shows metadata, and the chart route fetches the rest."""

    id: str
    sql: str
    owner_sub: str
    created_at: datetime
    sql_explanation: str = ""
    # OWUI conversation ID - the SPA groups charts with the searches from
    # the same chat. Empty when the caller didn't supply it.
    owui_chat_id: str = ""


class PlotDetail(BaseModel):
    """Spec and rows for the SPA's chart route, plus the SQL and its
    explanation for the "What this search matches" panel."""

    id: str
    spec: dict[str, Any]
    rows: list[dict[str, Any]]
    sql: str
    sql_explanation: str
    truncated: bool
