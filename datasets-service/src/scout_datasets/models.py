"""Pydantic request/response models for the public API."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import AliasChoices, BaseModel, Field


# Identifier columns we know how to materialize from. Order = preference:
# `message_control_id` > `accession_number` > `epic_mrn`. Caller can override
# via the request body. Patient cohorts (v1.1) will add `scout_patient_id`.
KNOWN_ID_COLUMNS: tuple[str, ...] = (
    "message_control_id",
    "accession_number",
    "epic_mrn",
)


class CreateDatasetRequest(BaseModel):
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
    # selected each row. Same list previously called
    # `positive_clinical_findings` — renamed because no classifier runs
    # anymore. Old name is still accepted as an alias for backwards
    # compatibility with the OWUI tool until it's redeployed.
    highlight_terms: list[str] | None = Field(
        default=None,
        description=(
            "Clinical terms to highlight in the row-expand viewer (e.g. "
            "['pulmonary embolism', 'PE']). UI-only; the SQL is what "
            "decides which rows are in the cohort. Anatomy/exam-type "
            "words don't belong here — those go in the SQL."
        ),
        # Accept BOTH the modern field name and the legacy
        # positive_clinical_findings alias. Pydantic v2 with bare
        # validation_alias= would lock the model to ONLY the alias,
        # silently dropping requests that send the canonical name —
        # exactly the bug that caused empty highlight_terms in storage.
        validation_alias=AliasChoices("highlight_terms", "positive_clinical_findings"),
        serialization_alias="highlight_terms",
    )
    sql_explanation: str | None = Field(
        default=None,
        description=(
            "Plain-language summary of what the SQL matches and why. "
            "Surfaced in the SPA's 'About this cohort' panel so the "
            "user can sanity-check the cohort definition without "
            "reading raw SQL. Example: 'Patients with chest CT reports "
            "from 2024 onwards that mention pulmonary nodules, "
            "excluding negated findings.' Optional but strongly "
            "encouraged."
        ),
    )
    owui_chat_id: str | None = Field(
        default=None,
        description=(
            "The OWUI conversation ID this cohort was created from. "
            "The SPA homepage groups datasets by chat so a user "
            "reviewing their work sees searches organized by the "
            "conversation that produced them."
        ),
    )
    owui_chat_title: str | None = Field(
        default=None,
        description=(
            "Snapshot of the OWUI chat title at create time. The chat "
            "title can change after the cohort exists; we freeze it so "
            "the SPA's section header stays stable."
        ),
    )
    llm_context_rows: int | None = Field(
        default=None,
        description="Max sample rows returned to the LLM (default 5).",
    )
    parent_dataset_id: str | None = Field(
        default=None,
        description=(
            "Optional lineage tag — when set, the new cohort's "
            "`parent_id` points at this dataset. The SPA homepage uses "
            "this to group refinement chains visually. No SQL-level "
            "effect — the new SQL stands alone (V1.1: no {{cohort}} "
            "placeholder, no substitution)."
        ),
    )


class CreateFromIdsRequest(BaseModel):
    """Materialize a cohort from an explicit list of identifiers
    (instead of running SQL to discover them). Used by the OWUI
    `import_cohort` tool when a researcher uploads a CSV of patient
    or accession IDs they want to search against — the tool parses
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
            "(e.g. 'Imported 234 accession numbers from research_cohort.csv')."
        ),
    )
    owui_chat_id: str | None = Field(default=None)
    owui_chat_title: str | None = Field(default=None)


class CreateFromIdsResponse(BaseModel):
    dataset_id: str
    count: int  # matched count encoded into the saved IN-list SQL
    submitted_count: int  # total IDs the caller submitted (before dedup/validate)
    unmatched_sample: list[str]  # up to 50 IDs we didn't find in reports_latest
    unmatched_total: int
    view_url: str
    summary: str  # LLM-bound markdown


class AggregateRequest(BaseModel):
    """One-shot SQL query — runs against Trino, returns rows directly,
    persists nothing. Backs the `scout_query_sql` tool surface for
    aggregate / COUNT / GROUP BY questions where the user wants prose,
    not a cohort viewer."""

    sql: str = Field(..., description="Trino SQL to execute.")
    row_cap: int = Field(
        default=500,
        ge=1,
        le=10000,
        description="Hard cap on returned rows so a runaway GROUP BY can't dump the world to chat context.",
    )


class AggregateResponse(BaseModel):
    columns: list[str]
    rows: list[dict[str, Any]]
    truncated: bool = Field(
        default=False,
        description="True when the query returned more than `row_cap` rows; rows were truncated to the cap.",
    )


class CreateDatasetResponse(BaseModel):
    dataset_id: str
    count: int
    id_column: str
    kind: str
    # The LLM-bound summary (count + columns + sample table +
    # anti-restatement directive + internal cohort handle note).
    summary: str
    # Per-row sample for the LLM's reasoning context. Big text columns
    # stripped server-side. JSON-safe (Trino types coerced).
    sample: list[dict[str, Any]]
    view_url: str


class DatasetMeta(BaseModel):
    dataset_id: str
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
    # Empty list when no positive_clinical_findings were passed.
    highlight_terms: list[str] = []
    # Plain-language summary of what the SQL matches and why,
    # written by the LLM at create time. Surfaced in the SPA's
    # "About this cohort" panel. Empty string if not provided.
    sql_explanation: str = ""
    # OWUI conversation ID + chat-title snapshot — drives the SPA
    # homepage's per-chat grouping. Empty when the caller didn't
    # supply them (legacy data or non-chat callers).
    owui_chat_id: str = ""
    owui_chat_title: str = ""


class RowsResponse(BaseModel):
    dataset_id: str
    page: int
    limit: int
    total: int
    columns: list[str]
    rows: list[dict[str, Any]]
