"""Pydantic request/response models for the public API."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# Identifier columns we know how to materialize from. Order = preference:
# `message_control_id` > `accession_number` > `epic_mrn`. Caller can override
# via the request body. Patient-scoped searches will add `scout_patient_id`.
KNOWN_ID_COLUMNS: tuple[str, ...] = (
    "message_control_id",
    "accession_number",
    "epic_mrn",
)


class CreateSearchRequest(BaseModel):
    sql: str = Field(
        ...,
        description="Trino SQL to materialize. SELECT must include one of the known identifier columns.",
    )
    kind: str = Field(
        default="report", description="`report` for now; `patient` lands in v1.1."
    )
    id_column: str | None = Field(
        default=None,
        description="Override the auto-picked identifier column. Must be present in the SELECT.",
    )

    # Clinical terms to highlight in the report-text viewer when a user
    # expands a row. The SQL itself does positive/negative filtering;
    # these are purely for UI emphasis so the user can see WHY the LLM
    # selected each row.
    highlight_terms: list[str] | None = Field(
        default=None,
        description=(
            "Clinical terms to highlight in the row-expand viewer (e.g. "
            "['pulmonary embolism', 'PE']). UI-only; the SQL is what "
            "decides which rows are in the search. Anatomy/exam-type "
            "words don't belong here — those go in the SQL."
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
    owui_chat_title: str | None = Field(
        default=None,
        description=(
            "Snapshot of the OWUI chat title at create time. The chat "
            "title can change after the search exists; we freeze it so "
            "the SPA's section header stays stable."
        ),
    )
    llm_context_rows: int | None = Field(
        default=None,
        description="Max sample rows returned to the LLM (default 5).",
    )
    parent_id: str | None = Field(
        default=None,
        description=(
            "Optional lineage tag — when set, the new search's "
            "`parent_id` points at this row. The SPA homepage uses "
            "this to group refinement chains visually. No SQL-level "
            "effect — the new SQL stands alone."
        ),
    )


class CreateFromFileRequest(BaseModel):
    """Materialize a search from an explicit list of identifiers (parsed
    out of an uploaded CSV/Excel file). Used by the OWUI
    `scout_find_reports` tool's file-upload branch — the tool parses
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
        description=(
            "Which column the ids map to. One of: "
            "'message_control_id', 'accession_number', 'epic_mrn'."
        ),
    )
    kind: str = Field(
        default="report",
        description="`report` for now; `patient` lands in v1.1.",
    )
    sql_explanation: str | None = Field(
        default=None,
        description=(
            "Plain-language summary of where the IDs came from "
            "(e.g. 'Imported 234 accession numbers from research_search.csv')."
        ),
    )
    owui_chat_id: str | None = Field(default=None)
    owui_chat_title: str | None = Field(default=None)


class CreateFromFileResponse(BaseModel):
    id: str
    count: int  # matched count encoded into the saved IN-list SQL
    submitted_count: int  # total IDs the caller submitted (before dedup/validate)
    unmatched_sample: list[str]  # up to 50 IDs we didn't find in reports_latest
    unmatched_total: int
    view_url: str
    summary: str  # LLM-bound markdown


class QueryRequest(BaseModel):
    """One-shot SQL query — runs against Trino, returns rows directly,
    persists nothing. Backs the `scout_query_sql` tool surface for
    aggregate / COUNT / GROUP BY questions where the user wants prose,
    not a search viewer."""

    sql: str = Field(..., description="Trino SQL to execute.")
    row_cap: int = Field(
        default=500,
        ge=1,
        le=10000,
        description="Hard cap on returned rows so a runaway GROUP BY can't dump the world to chat context.",
    )


class QueryResponse(BaseModel):
    columns: list[str]
    rows: list[dict[str, Any]]
    truncated: bool = Field(
        default=False,
        description="True when the query returned more than `row_cap` rows; rows were truncated to the cap.",
    )


class ReadReportsRequest(BaseModel):
    """Fetch the full content of specific reports by ID. Backs the
    `scout_get_reports` tool surface AND the SPA row-expand panel
    (which sends an array of one)."""

    ids: list[str] = Field(..., description="Report identifiers to fetch.")
    id_column: str = Field(
        default="message_control_id",
        description="Column to match `ids` against.",
    )


class ReadReportsResponse(BaseModel):
    columns: list[str]
    rows: list[dict[str, Any]]


class CreateSearchResponse(BaseModel):
    id: str
    count: int
    id_column: str
    kind: str
    # The LLM-bound summary (count + columns + sample table +
    # anti-restatement directive + internal search handle note).
    summary: str
    # Per-row sample for the LLM's reasoning context. Big text columns
    # stripped server-side. JSON-safe (Trino types coerced).
    sample: list[dict[str, Any]]
    view_url: str


class SearchMeta(BaseModel):
    id: str
    kind: str
    id_column: str
    count: int
    parent_id: str | None
    source_sql: str
    owner_sub: str
    created_at: datetime
    expires_at: datetime
    last_read_at: datetime
    # Clinical terms the LLM was searching for at create time —
    # surfaced to the SPA so the row-expand panel can highlight
    # them in the report text without the user typing anything.
    highlight_terms: list[str] = []
    # Plain-language summary of what the SQL matches and why,
    # written by the LLM at create time. Surfaced in the SPA's
    # "About this search" panel. Empty string if not provided.
    sql_explanation: str = ""
    # OWUI conversation ID + chat-title snapshot — drives the SPA
    # homepage's per-chat grouping. Empty when the caller didn't
    # supply them.
    owui_chat_id: str = ""
    owui_chat_title: str = ""


class RowsResponse(BaseModel):
    id: str
    page: int
    limit: int
    total: int
    columns: list[str]
    rows: list[dict[str, Any]]
