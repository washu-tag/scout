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
    "mpi",
    "scout_patient_id",
)

# Every saved /searches SQL must project these in its outer SELECT.
SEARCH_REQUIRED_COLUMNS: tuple[str, ...] = (
    "primary_report_identifier",
    "accession_number",
)

# Patient-scoped IDs go through reports_latest_epic_view; epic_mrn / mpi
# transparently match the resolved_* columns so reports missing the raw
# value still come back when the same patient appears elsewhere.
PATIENT_ID_COLUMNS: dict[str, str] = {
    "epic_mrn": "resolved_epic_mrn",
    "mpi": "resolved_mpi",
    "scout_patient_id": "scout_patient_id",
}


class CreateSearchRequest(BaseModel):
    sql: str = Field(
        ...,
        description=(
            "Trino SQL to save. Outer SELECT must project "
            "primary_report_identifier and accession_number."
        ),
    )
    match_terms: list[str] | None = Field(
        default=None,
        description=(
            "Clinical text terms (e.g. ['pulmonary embolism', 'PE']) "
            "matched against report sections to produce the `excerpt` "
            "field on each evidence row, and highlighted in the "
            "row-expand viewer. Matched with word boundaries on the "
            "SPA side. Anatomy/exam-type words belong in the SQL, "
            "not here."
        ),
    )
    match_diagnoses: list[str] | None = Field(
        default=None,
        description=(
            "ICD codes (or code prefixes) matched against "
            "`diagnosis_code` to populate `matched_diagnoses` on each "
            "evidence row, and surfaced as chips in the row-expand "
            "viewer. Examples: ['R91.1'], ['J18', 'R91']. UI/evidence "
            "only; the SQL still drives inclusion."
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


class CreateFromFileRequest(BaseModel):
    """Materialize a search from an explicit list of identifiers (parsed
    out of an uploaded CSV/Excel file). Used by the OWUI
    `scout_find_reports` tool's file-upload branch - the tool parses
    the file, extracts IDs, and POSTs them here.

    Backend validates each id exists in reports_latest, returns the
    matched count + a sample of unmatched ids so the LLM can surface
    which entries weren't found.
    """

    ids: list[str] = Field(
        ...,
        description="The identifier list to materialize. Deduplicated before insert.",
    )
    id_column: str = Field(
        ...,
        description=f"Which column the ids map to. One of: {list(INPUT_ID_COLUMNS)}.",
    )
    sql_explanation: str | None = Field(
        default=None,
        description=(
            "Plain-language summary of where the IDs came from "
            "(e.g. 'Imported 234 accession numbers from research_search.csv')."
        ),
    )
    owui_chat_id: str | None = Field(default=None)


class CreateFromFileResponse(BaseModel):
    id: str
    count: int
    id_column: str
    submitted_count: int
    unmatched_sample: list[str]
    unmatched_total: int
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


class ReadReportsRequest(BaseModel):
    """Fetch the full content of specific reports by ID. Backs the
    `scout_get_reports` tool surface AND the SPA row-expand panel
    (which sends an array of one)."""

    ids: list[str] = Field(..., description="Report identifiers to fetch.")
    id_column: str = Field(
        default="primary_report_identifier",
        description="Column to match `ids` against.",
    )


class ReadReportsResponse(BaseModel):
    columns: list[str]
    rows: list[dict[str, Any]]


class CreateSearchResponse(BaseModel):
    id: str
    count: int
    id_column: str
    view_url: str
    columns: list[str]
    sample: list[dict[str, Any]]
    # Parallel-indexed to `sample`. Each item is {id_column: value,
    # excerpt: str | None, matched_diagnoses: list[{code, text}]}.
    evidence: list[dict[str, Any]]


class SearchMeta(BaseModel):
    id: str
    id_column: str
    count: int
    sql: str
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
    id: str
    page: int
    limit: int
    total: int
    columns: list[str]
    rows: list[dict[str, Any]]
