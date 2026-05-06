"""
title: Scout Query Tool
description: Two LLM-callable functions for the Scout cohort-building chat —
             search_reports (find reports by criteria, returns metadata +
             optional snippets, no full text) and read_reports (full
             findings/impression for specific reports or sampled from a saved
             cohort). search_reports persists a small recipe + identity-list
             artifact when the result has identifier columns; read_reports
             consumes those artifacts and writes none. Submission to XNAT is
             still the Send-to-XNAT Action on the message toolbar.
author: Scout Team
version: 0.3.0
"""

from __future__ import annotations

import asyncio
import html as html_module
import inspect
import io
import json
import logging
import random
import re
import secrets
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)


# ============================================================================
# Assertion classification — pure-regex, no external NLP libs. Three wins
# over the original 50-char-before window:
#   1. Sentence scope (split on .!?). Negation cue and matched term must be
#      in the SAME sentence — kills the cross-sentence "no" false matches.
#   2. Section stripping. Drop HISTORY / INDICATIONS / TECHNIQUE / COMPARISON
#      / REASON sections before scanning — those describe WHY the study was
#      ordered, not findings, and are the #1 source of false positives.
#   3. Termination cues. "but / however / except / although" close the
#      negation scope; "no focal mass, but mild hyperdensity" includes the
#      hyperdensity finding.
# Patterns ported from analytics/notebooks/cohort/cohort_builder.py — keep
# both files in sync.
# ============================================================================

NEGATION_PATTERNS = [
    r"\bno\s+(mri?\s+)?evidence\b",
    r"\bwithout\s+(mri?\s+)?evidence\b",
    r"\bnegative\s+for\b",
    r"\babsence\s+of\b",
    r"\bruled?\s+out\b",
    r"\brules?\s+out\b",
    r"\bexcluding\b",
    r"\bexcluded\b",
    r"\bno\s+\w+\s+(suggest|indication|sign)\b",
    r"\bnot\s+(seen|present|identified|visualized|noted|appreciated)\b",
    r"\b(?:does|did|do)\s+not\b",
    r"\bno\s+",
]

UNCERTAINTY_PATTERNS = [
    r"\bevaluat(?:e|ion)\s+for\b",
    r"\bconcern(?:ing)?\s+for\b",
    r"\bsuspect(?:ed|ious)?\b",
    r"\bsuggesting\b",
    r"\bsuggestive\s+of\b",
    r"\bpossib(?:le|ly)\b",
    r"\bquestion(?:able|ed)?\b",
    r"\bcannot\s+(?:be\s+)?exclud(?:e|ed)\b",
    r"\b(?:vs\.?|versus)\b",
    r"\bdifferential\b",
    r"\brule\s+out\b",  # "rule out PE" = uncertainty, not negation
    r"\br/o\b",
]

HISTORICAL_PATTERNS = [
    r"\bhistory\s+of\b",
    r"\bh/o\b",
    r"\bprior\b",
    r"\bprevious(?:ly)?\b",
    r"\bremote\b",
    r"\bs/p\b",
    r"\bstatus\s+post\b",
    r"\bpast\s+medical\s+history\b",
]

# Termination cues close negation scope mid-sentence. "no focal mass, but
# there is X" → X is a positive finding.
TERMINATION_PATTERNS = [
    r"\bbut\b",
    r"\bhowever\b",
    r"\bexcept\b",
    r"\balthough\b",
    r"\baside\s+from\b",
    r"\bother\s+than\b",
]

# Section headers we ignore — describe WHY the study was done, not findings.
NON_FINDING_SECTIONS = (
    "HISTORY",
    "INDICATION",
    "INDICATIONS",
    "TECHNIQUE",
    "COMPARISON",
    "REASON FOR EXAM",
    "REASON FOR EXAMINATION",
    "CLINICAL HISTORY",
    "CLINICAL INFORMATION",
    "PRIOR EXAM",
    "PROCEDURE",
)
# Section headers we keep — actual findings.
FINDING_SECTIONS = (
    "FINDINGS",
    "IMPRESSION",
    "IMPRESSIONS",
    "ADDENDUM",
    "ADDENDA",
    "RESULT",
    "RESULTS",
    "REPORT",
    "CONCLUSION",
    "INTERPRETATION",
)
_ALL_SECTION_HEADERS = NON_FINDING_SECTIONS + FINDING_SECTIONS

_NEGATION_RE = re.compile("|".join(NEGATION_PATTERNS), re.IGNORECASE)
_UNCERTAINTY_RE = re.compile("|".join(UNCERTAINTY_PATTERNS), re.IGNORECASE)
_HISTORICAL_RE = re.compile("|".join(HISTORICAL_PATTERNS), re.IGNORECASE)
_TERMINATION_RE = re.compile("|".join(TERMINATION_PATTERNS), re.IGNORECASE)
# Naive sentence boundary; preserves the punctuation in the sentence text.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")
_SECTION_HEADER_RE = re.compile(
    r"^\s*(" + "|".join(re.escape(h) for h in _ALL_SECTION_HEADERS) + r")\s*:",
    re.IGNORECASE | re.MULTILINE,
)

_SHORT_TOKEN_PATTERN = re.compile(r"^[A-Za-z]{1,4}$")
_REGEX_META_CHARS = set(r"\^$.|?*+()[]{}")


def _normalize_search_pattern(pattern: str) -> tuple[str, bool]:
    """Auto-wrap short bare alphabetic tokens in word boundaries.

    `"PE"` matches "PET-CT", "performed", "upper", etc. — false-positive city.
    `\\bPE\\b` matches only standalone "PE". Almost every medical acronym a
    researcher passes (PE, MS, RA, MI, CHF, COPD) wants this. Patterns
    containing regex metacharacters are left alone (the user knows what
    they're doing). Returns (normalized, was_wrapped) so callers can tell
    the user we adjusted the pattern."""
    if not pattern:
        return pattern, False
    if any(ch in _REGEX_META_CHARS for ch in pattern):
        return pattern, False
    if _SHORT_TOKEN_PATTERN.match(pattern):
        return rf"\b{pattern}\b", True
    return pattern, False


def _normalize_search_patterns(
    patterns: list[str],
) -> tuple[list[str], list[tuple[str, str]]]:
    """Normalize a list of patterns. Returns (normalized_list, wrapped_pairs)
    where wrapped_pairs is [(original, normalized), ...] for any auto-wrapped
    entries — surfaced in the tool summary so the LLM/user can see what we
    changed."""
    out: list[str] = []
    wrapped: list[tuple[str, str]] = []
    for p in patterns:
        norm, was = _normalize_search_pattern(p)
        out.append(norm)
        if was:
            wrapped.append((p, norm))
    return out, wrapped


_NEGATION_NEEDS_TEXT_COL_ERROR = (
    "positive_terms was set but the SELECT does not include `report_text`. "
    "The assertion classifier scans `report_text` for matches and their "
    "surrounding sentence — it can't run without the column. Re-run with "
    "`report_text` in the SELECT."
)


def _strip_non_finding_sections(text: str) -> str:
    """Remove HISTORY/INDICATIONS/TECHNIQUE/COMPARISON sections from `text`.
    If no section headers are found, returns the original text unchanged.
    Always preserves any text before the first section header (often the
    report title)."""
    matches = list(_SECTION_HEADER_RE.finditer(text))
    if not matches:
        return text
    non_finding = {h.upper() for h in NON_FINDING_SECTIONS}
    keep = [text[: matches[0].start()]]
    for i, m in enumerate(matches):
        header = m.group(1).upper()
        section_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        if header not in non_finding:
            keep.append(text[m.start() : section_end])
    return "\n".join(p for p in keep if p)


def _split_sentences(text: str) -> list[str]:
    """Naive sentence split on .!? followed by whitespace+capital. Good
    enough for radiology narrative; medical abbrevs (Dr., 1.5 cm) cause
    occasional missed splits but those don't hurt assertion classification."""
    parts = _SENTENCE_SPLIT_RE.split(text)
    return [p for p in parts if p.strip()]


def _classify_match_in_sentence(
    sentence: str, match_start: int, match_end: int
) -> tuple[str, str]:
    """Classify a single match within its sentence. Returns (label, excerpt).
    Label is 'positive' / 'negated' / 'uncertain' / 'historical'.

    Negation cues can come BEFORE or AFTER the match (handles "no PE" and
    "PE: not seen"). Termination cues between a leading negation cue and
    the match close the negation scope ("no focal mass, but PE present" →
    PE is positive)."""
    pre = sentence[:match_start]
    post = sentence[match_end:]

    # Termination cue closes leading negation scope. Find the LAST term-cue
    # before the match — only negation cues AFTER that survive.
    term_match = None
    for tm in _TERMINATION_RE.finditer(pre):
        term_match = tm
    pre_active = pre[term_match.end() :] if term_match else pre

    if _NEGATION_RE.search(pre_active) or _NEGATION_RE.search(post):
        return "negated", _excerpt(sentence, match_start, match_end)
    if _UNCERTAINTY_RE.search(pre) or _UNCERTAINTY_RE.search(post):
        return "uncertain", _excerpt(sentence, match_start, match_end)
    if _HISTORICAL_RE.search(pre):
        return "historical", _excerpt(sentence, match_start, match_end)
    return "positive", _excerpt(sentence, match_start, match_end)


def _excerpt(sentence: str, start: int, end: int, pad: int = 60) -> str:
    s = max(0, start - pad)
    e = min(len(sentence), end + pad)
    return f"…{sentence[s:e].strip()}…"


def _highlight_text(text: Optional[str], search_patterns: list[str]) -> str:
    """HTML-escape `text` and wrap each match in a green span (positive) or
    red span (negated/uncertain/historical) using the same regex pipeline
    as `_apply_negation`. Falls through to plain escape if no matches."""
    if not text:
        return ""
    if not search_patterns:
        return html_module.escape(text)
    norm_patterns, _ = _normalize_search_patterns(search_patterns)
    pattern_re = re.compile(
        "|".join(f"(?:{p})" for p in norm_patterns), re.IGNORECASE | re.DOTALL
    )
    sentences = _split_sentences(text)
    # Re-find each sentence's offsets in the original text so highlight
    # positions line up.
    out: list[str] = []
    cursor = 0
    for sent in sentences:
        idx = text.find(sent, cursor)
        if idx < 0:
            continue
        out.append(html_module.escape(text[cursor:idx]))
        sent_cursor = 0
        for m in pattern_re.finditer(sent):
            label, _ = _classify_match_in_sentence(sent, m.start(), m.end())
            cls = "hl-pos" if label == "positive" else "hl-neg"
            out.append(html_module.escape(sent[sent_cursor : m.start()]))
            out.append(
                f'<span class="{cls}">{html_module.escape(sent[m.start():m.end()])}</span>'
            )
            sent_cursor = m.end()
        out.append(html_module.escape(sent[sent_cursor:]))
        cursor = idx + len(sent)
    out.append(html_module.escape(text[cursor:]))
    return "".join(out)


def _apply_negation(
    rows: list[dict],
    search_patterns: list[str],
    text_column: str = "report_text",
) -> tuple[bool, int, int, list[tuple[str, str]]]:
    """Classify each row's report_text against the search patterns using
    sentence-scoped regex with section stripping and termination cues.

    Returns (was_applied, included_count, excluded_count, wrapped_pairs).
    Each row gets `included`, `why_included`, `why_excluded`, and
    `assertion_label` ∈ {positive, negated, uncertain, historical,
    not_mentioned}.

    A row is included iff at least one match of any pattern is classified
    `positive` (no leading negation/uncertainty/history).

    Raises ValueError if `search_patterns` is set but rows lack `text_column`."""
    if not search_patterns:
        for row in rows:
            row["included"] = True
            row["why_excluded"] = ""
            row["why_included"] = ""
        return False, len(rows), 0, []

    if rows and not any(text_column in r for r in rows):
        raise ValueError(_NEGATION_NEEDS_TEXT_COL_ERROR)

    norm_patterns, wrapped = _normalize_search_patterns(search_patterns)
    pattern_re = re.compile(
        "|".join(f"(?:{p})" for p in norm_patterns), re.IGNORECASE | re.DOTALL
    )

    included = 0
    for row in rows:
        text = row.get(text_column) or ""
        if not text:
            row["included"] = False
            row["why_excluded"] = "(no report_text on this row)"
            row["why_included"] = ""
            row["assertion_label"] = "not_mentioned"
            continue
        scan_text = _strip_non_finding_sections(text)
        sentences = _split_sentences(scan_text)
        positive_excerpt: Optional[str] = None
        first_disqualified: Optional[tuple[str, str]] = None
        for sent in sentences:
            for m in pattern_re.finditer(sent):
                label, excerpt = _classify_match_in_sentence(sent, m.start(), m.end())
                if label == "positive":
                    positive_excerpt = excerpt
                    break
                if first_disqualified is None:
                    first_disqualified = (label, excerpt)
            if positive_excerpt is not None:
                break
        if positive_excerpt is not None:
            row["included"] = True
            row["why_included"] = positive_excerpt
            row["why_excluded"] = ""
            row["assertion_label"] = "positive"
            included += 1
        elif first_disqualified is not None:
            label, excerpt = first_disqualified
            row["included"] = False
            row["why_included"] = ""
            row["why_excluded"] = f"[{label.upper()}] {excerpt}"
            row["assertion_label"] = label
        else:
            row["included"] = False
            row["why_included"] = ""
            row["why_excluded"] = "(search term not present in report_text)"
            row["assertion_label"] = "not_mentioned"
    return True, included, len(rows) - included, wrapped


# ============================================================================
# Artifacts — inlined from scout_artifacts.py. Recipe + rows protocol per
# scout-tool-design.md §5. Tag is `meta.scout_result=True, meta.chat_id=<chat>`.
# ============================================================================

SCOUT_RESULT_META_KEY = "scout_result"
VALUES_SHAPE_THRESHOLD = 5000
SUBSTITUTION_COLUMN_PREFERENCE = ("message_control_id", "accession_number", "epic_mrn")
COHORT_PLACEHOLDER = "{{cohort}}"


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


async def _write_search_artifact(
    recipe: dict, rows: list[dict], user_id: str, chat_id: str
) -> str:
    return await _write_artifact({"recipe": recipe, "rows": rows}, user_id, chat_id)


async def _write_artifact(payload: dict, user_id: str, chat_id: str) -> str:
    if not user_id:
        log.warning("scout_artifacts: no user_id; skipping write")
        return ""
    from open_webui.models.files import Files, FileForm
    from open_webui.storage.provider import Storage

    # cohort_id format: `coh_<6 url-safe chars>`. Self-identifying prefix
    # disambiguates from accession_number / message_control_id / MRN in the
    # LLM's context. ~57B combinations — collision-free at our scale.
    file_id = "coh_" + secrets.token_urlsafe(5)[:6]
    filename = f"scout_result_{file_id}.json"
    payload = {
        "file_id": file_id,
        "chat_id": chat_id,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        **payload,
    }
    body = json.dumps(payload, default=str).encode("utf-8")
    _contents, file_path = await _maybe_await(
        Storage.upload_file(io.BytesIO(body), filename, {})
    )
    await _maybe_await(
        Files.insert_new_file(
            user_id,
            FileForm(
                id=file_id,
                filename=filename,
                path=file_path,
                meta={
                    "name": filename,
                    "content_type": "application/json",
                    "size": len(body),
                    "chat_id": chat_id,
                    SCOUT_RESULT_META_KEY: True,
                },
            ),
        )
    )
    return file_id


async def _read_artifact(cohort_id: str) -> Optional[dict]:
    if not cohort_id:
        return None
    try:
        from open_webui.models.files import Files
        from open_webui.storage.provider import Storage
    except Exception:
        log.exception("scout_artifacts: open_webui imports failed")
        return None
    try:
        file_record = await _maybe_await(Files.get_file_by_id(cohort_id))
        if file_record is None:
            return None
        path = getattr(file_record, "path", None)
        if not path:
            return None
        contents = await _maybe_await(Storage.get_file(path))
        if isinstance(contents, str):
            with open(contents, "rb") as fh:
                contents = fh.read()
        if hasattr(contents, "read"):
            contents = contents.read()
        return json.loads(contents)
    except Exception:
        log.exception("scout_artifacts: failed to read %s", cohort_id)
        return None


async def _find_recent_artifacts(user_id: str, chat_id: str) -> list:
    if not user_id or not chat_id:
        return []
    try:
        from open_webui.models.files import Files
    except Exception:
        return []
    all_files = await _maybe_await(Files.get_files_by_user_id(user_id))
    ours = [
        f
        for f in all_files
        if (f.meta or {}).get("chat_id") == chat_id
        and (f.meta or {}).get(SCOUT_RESULT_META_KEY)
    ]
    ours.sort(key=lambda f: f.created_at or 0, reverse=True)
    return ours


def _pick_substitution_column(rows: list[dict]) -> Optional[str]:
    if not rows:
        return None
    first = rows[0] or {}
    for col in SUBSTITUTION_COLUMN_PREFERENCE:
        if col in first and first[col] is not None:
            return col
    return None


def _sql_escape(value: str) -> str:
    return value.replace("'", "''")


def _render_in_clause(column: str, values: list[str]) -> str:
    quoted = [f"'{_sql_escape(v)}'" for v in values]
    if len(values) <= VALUES_SHAPE_THRESHOLD:
        return f"{column} IN ({', '.join(quoted)})"
    rows_clause = ", ".join(f"({q})" for q in quoted)
    return f"{column} IN (SELECT v FROM (VALUES {rows_clause}) AS t(v))"


def _substitute_cohort(sql: str, artifact: dict) -> str:
    if COHORT_PLACEHOLDER not in sql:
        raise ValueError(
            f"cohort_id was set but SQL has no {COHORT_PLACEHOLDER!r} placeholder. "
            f"Add {COHORT_PLACEHOLDER} to your WHERE clause where the cohort filter belongs."
        )
    rows = artifact.get("rows") or []
    column = _pick_substitution_column(rows)
    if not column:
        raise ValueError(
            f"Cohort artifact has no recognizable identifier column "
            f"(expected one of {SUBSTITUTION_COLUMN_PREFERENCE})."
        )
    has_included = any("included" in r for r in rows)
    if has_included:
        values = [
            str(r[column])
            for r in rows
            if r.get("included") and r.get(column) is not None
        ]
    else:
        values = [str(r[column]) for r in rows if r.get(column) is not None]
    if not values:
        raise ValueError("Cohort artifact has no included rows to substitute.")
    return sql.replace(COHORT_PLACEHOLDER, _render_in_clause(column, values))


async def _prune_artifacts(user_id: str, chat_id: str, keep_n: int) -> None:
    try:
        from open_webui.models.files import Files
        from open_webui.storage.provider import Storage
    except Exception:
        return
    try:
        ours = await _find_recent_artifacts(user_id, chat_id)
    except Exception:
        log.exception("scout_artifacts: prune list failed")
        return
    for old in ours[keep_n:]:
        try:
            await _maybe_await(Storage.delete_file(old.path))
        except Exception:
            log.exception("scout_artifacts: storage delete failed for %s", old.id)
        try:
            await _maybe_await(Files.delete_file_by_id(old.id))
        except Exception:
            log.exception("scout_artifacts: record delete failed for %s", old.id)


# ============================================================================
# Constants & helpers specific to this Tool
# ============================================================================

# Columns we strip from LLM-bound rows in search_reports. The full text stays
# in Trino — the LLM scans by metadata + optional `snippet_around` windows
# rather than by drinking the firehose.
TEXT_COLUMNS_TO_STRIP = {
    "report_text",
    "report_section_findings",
    "report_section_impression",
    "report_section_addendum",
    "report_section_technician_note",
}

# Identifier columns; presence in result rows triggers artifact persistence.
PERSISTENCE_TRIGGER_COLUMNS = {"message_control_id", "accession_number", "epic_mrn"}

# Columns we keep on artifact rows alongside any present identifiers and the
# included/why_excluded flags. We don't store the metadata columns the LLM
# already saw — the artifact is a recipe + identity, not a row dump.
ARTIFACT_ROW_KEYS = (
    "message_control_id",
    "accession_number",
    "epic_mrn",
    "included",
    "why_excluded",
    "why_included",
    "assertion_label",
)

VALID_SEARCH_DISPLAYS = {"none", "table"}
VALID_READ_DISPLAYS = {"none", "detail", "table"}

DISPLAY_LLM_ROW_DEFAULTS = {
    "none": None,
    "table": 5,
    "detail": None,
}

# Render caps. The iframe HTML ends up in the assistant message stored in
# chat history (and pushed through Socket.IO). Without caps, a 500-row search
# with full text produces multi-MB payloads that stall the websocket. The
# Download CSV button was removed entirely for the same reason — its base64
# blob was the dominant chunk.
MAX_RENDERED_TABLE_ROWS = 50
MAX_RENDERED_DETAIL_CARDS = 20
MAX_DETAIL_SECTION_CHARS = 3000  # per-section text cap inside a rendered card
# read_reports puts FULL findings/impression text for ONLY this many reports
# into the LLM's context. Other rows are one-line identifiers + the cohort's
# why_included / why_excluded excerpt (the assertion classifier's snippet,
# already stored on the artifact for free). The user still sees every report
# rendered as a card in the iframe; this constant only bounds LLM context.
# Keeping it at 1 forces the LLM to focus on one report's narrative at a time.
MAX_LLM_READ_FULL_TEXT_REPORTS = 1

# Sentence boundary used by snippet_around. Match a period, exclamation, or
# question mark followed by whitespace or end-of-string. The negative lookbehind
# avoids splitting on common abbreviations (Dr., Mr., etc.) and decimal points.
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])(?<![A-Z][a-z]\.)\s+")


def _extract_snippets(
    text: Optional[str], pattern: str, window_sentences: int = 1
) -> list[str]:
    """Return ±N sentence windows around each non-overlapping match of `pattern`
    in `text`. Used by search_reports's `snippet_around` to give the LLM enough
    context to triage without sending the whole report."""
    if not text or not pattern:
        return []
    try:
        compiled = re.compile(pattern, re.IGNORECASE | re.DOTALL)
    except re.error:
        return []

    sentences = _SENTENCE_BOUNDARY.split(text)
    sentence_spans: list[tuple[int, int, str]] = []
    cursor = 0
    for sentence in sentences:
        if not sentence:
            continue
        start = text.find(sentence, cursor)
        if start < 0:
            start = cursor
        end = start + len(sentence)
        sentence_spans.append((start, end, sentence))
        cursor = end

    snippets: list[str] = []
    seen_indices: set[int] = set()
    for match in compiled.finditer(text):
        for idx, (start, end, _) in enumerate(sentence_spans):
            if start <= match.start() < end:
                lo = max(0, idx - window_sentences)
                hi = min(len(sentence_spans), idx + window_sentences + 1)
                key = (lo, hi)
                if key in seen_indices:
                    break
                seen_indices.add(key)
                window = " ".join(s for _, _, s in sentence_spans[lo:hi]).strip()
                if window:
                    snippets.append(window)
                break
    return snippets


# ============================================================================
# Tools
# ============================================================================


class Tools:
    """Search reports and read full text. See module docstring for the
    search/read split rationale."""

    class Valves(BaseModel):
        trino_host: str = Field(default="trino", description="Trino hostname")
        trino_port: int = Field(default=8080, description="Trino port")
        trino_user: str = Field(default="trino", description="Trino user")
        trino_catalog: str = Field(default="delta", description="Trino catalog")
        trino_schema: str = Field(default="default", description="Trino schema")
        safety_max_context_rows: int = Field(
            default=200,
            description="Hard cap on rows sent to LLM context regardless of llm_context_rows",
        )
        owui_files_lru_keep: int = Field(
            default=10,
            description="Last N Scout artifacts to retain per chat in OWUI Files",
        )

    def __init__(self) -> None:
        self.valves = self.Valves()

    # ------------------------------------------------------------------
    # search_reports — find reports by criteria
    # ------------------------------------------------------------------
    async def search_reports(
        self,
        sql: str,
        display: str = "table",
        cohort_id: Optional[str] = None,
        snippet_around: Optional[str] = None,
        positive_terms: Optional[list[str]] = None,
        llm_context_rows: Optional[int] = None,
        __user__: Optional[dict] = None,
        __event_emitter__: Optional[Callable[[Any], Awaitable[None]]] = None,
        __chat_id__: str = "",
    ) -> Any:
        """
        Search Scout's radiology reports for a question. Returns metadata rows
        (and optional snippets) to your context — never full report text. When
        the result has identifier columns, persists a small saved-cohort
        record (recipe + identifiers + eligibility flags, NOT the full result)
        and returns a `cohort_id` you can chain into a follow-up call.

        Use this tool to DISCOVER which rows match a question. Use
        `read_reports` to fetch full text. After building a cohort, the
        natural follow-up is `read_reports(cohort_id=..., included=True/False,
        sample='random')` for a spot-check.

        Prefer specific column projections over `SELECT *` — the canonical
        row-level cohort SELECT is `message_control_id, accession_number,
        modality, service_name, message_dt, patient_age, sex`, plus
        `report_text` when `positive_terms` is set. The Scout schema is in
        your system prompt; you generally don't need to query for it
        (`DESCRIBE`, `SHOW COLUMNS`).

        :param sql: Trino SQL against `delta.default.reports_latest` (deduped
            to final-per-accession). Always filter on the `year` partition.
            Include `message_control_id` and/or `accession_number` in
            row-level SELECTs so the result is chainable / exportable. When
            `positive_terms` is set, also include `report_text` — the
            classifier scans it.
        :param display: How to render to the user. Two modes:
            - "none" — no iframe. Use for aggregates (counts, breakdowns,
              GROUP BY queries); answer in prose with a markdown table if
              the user wants the numbers.
            - "table" — paginated iframe with row tinting (✓ included,
              ✗ excluded) and the cohort_id chip. Use this for row-level
              cohort searches where the user benefits from seeing the actual
              rows. Default.
            Detail mode (`detail`) lives on `read_reports`.
        :param cohort_id: A `cohort_id` from a previous `search_reports` or
            `load_id_list` call (format: `coh_<6 chars>`). When set, your SQL
            MUST include the literal token `{{cohort}}` in its WHERE clause —
            the tool substitutes that with `<id_col> IN ('...')` before
            running. Substitution column is auto-picked: most-specific
            (`message_control_id` > `accession_number` > `epic_mrn`).
        :param snippet_around: Optional regex. For each result row, the tool
            extracts ±1 sentence around matches in `report_text` and attaches
            them to the row as a `snippets` array.
        :param positive_terms: List of positive search terms you used in your
            SQL (e.g. `["pulmonary embolism", "PE"]`). The tool sentence-scans
            `report_text` for these terms and classifies each match as
            positive / negated / uncertain / historical (with section
            stripping for HISTORY/INDICATIONS/TECHNIQUE/COMPARISON and
            termination cues for `but`/`however`/`except`). Rows with no
            positive match are excluded from the cohort.

            Short bare alphabetic tokens (≤4 chars, no regex metacharacters)
            are auto-wrapped in word boundaries — `"PE"` is run as `\\bPE\\b`
            so it doesn't match `PET-CT`, `performed`, `upper`. Pass a regex
            with metacharacters (e.g. `r"P\\.?E\\.?"`) to opt out.

            Excluded rows stay in the saved cohort so you can spot-check via
            `read_reports(cohort_id=..., included=False)`.
        :param llm_context_rows: Override the number of sample rows returned
            to your context. Defaults: 5 for table, all for none. Capped by
            `safety_max_context_rows`.
        :return: Summary text (LLM-bound) + iframe (user-bound). The summary
            leads with the new `cohort_id` when one is saved.

        Examples:

        Aggregate (no identifiers → no cohort saved; render in prose / markdown):
            search_reports(
                sql="SELECT year, COUNT(*) FROM delta.default.reports_latest "
                    "WHERE year >= 2020 AND modality='CT' GROUP BY year",
                display="none")

        Text-search cohort with assertion classification (saves a cohort):
            search_reports(
                sql='''SELECT message_control_id, accession_number, modality,
                              service_name, message_dt, report_text
                       FROM delta.default.reports_latest
                       WHERE year >= 2020
                         AND LOWER(report_text) LIKE '%pulmonary embolism%' ''',
                display="table",
                snippet_around="pulmonary embolism",
                positive_terms=["pulmonary embolism", "PE"])

        Chained call (use a cohort_id from a previous step, e.g. an upload):
            search_reports(
                sql='''SELECT message_control_id, accession_number, message_dt
                       FROM delta.default.reports_latest
                       WHERE service_name LIKE '%MR%PELVIS%' AND {{cohort}}''',
                cohort_id="coh_aB3zX9",
                display="table")
        """
        if __user__ is None:
            __user__ = {}
        if positive_terms is None:
            positive_terms = []

        if display not in VALID_SEARCH_DISPLAYS:
            display = "table"

        # ------------------------------------------------------------------
        # 1. Substitute {{cohort}} if cohort_id is set.
        # ------------------------------------------------------------------
        substituted_sql = sql
        if cohort_id:
            artifact = await _read_artifact(cohort_id)
            if artifact is None:
                return f"Error: cohort {cohort_id!r} not found."
            try:
                substituted_sql = _substitute_cohort(sql, artifact)
            except ValueError as exc:
                return f"Error: {exc}"

        await self._emit_status(__event_emitter__, "Querying Trino…", done=False)

        # ------------------------------------------------------------------
        # 2. Execute Trino.
        # ------------------------------------------------------------------
        try:
            rows, columns = await self._execute_trino_query(substituted_sql)
        except Exception as exc:
            log.exception("Trino query failed")
            await self._emit_status(
                __event_emitter__, f"Query failed: {exc}", done=True
            )
            return f"Error executing query: {exc}"

        if not rows:
            await self._emit_status(
                __event_emitter__, "Query complete — no results", done=True
            )
            return "The query returned no results."

        # ------------------------------------------------------------------
        # 3. Apply negation (annotates rows with included / why_excluded).
        # ------------------------------------------------------------------
        try:
            (
                negation_applied,
                included_count,
                excluded_count,
                wrapped_patterns,
            ) = _apply_negation(rows, positive_terms)
        except ValueError as exc:
            await self._emit_status(__event_emitter__, str(exc), done=True)
            return f"Error: {exc}"

        # ------------------------------------------------------------------
        # 4. Snippet extraction (if requested).
        # ------------------------------------------------------------------
        if snippet_around:
            for row in rows:
                row["snippets"] = _extract_snippets(
                    row.get("report_text"), snippet_around
                )

        # ------------------------------------------------------------------
        # 5. Persist saved cohort if identifier columns are present in results.
        # ------------------------------------------------------------------
        saved_cohort_id = ""
        has_identifier_columns = bool(set(columns) & PERSISTENCE_TRIGGER_COLUMNS)
        user_id = __user__.get("id", "") if isinstance(__user__, dict) else ""
        if has_identifier_columns and user_id and __chat_id__:
            try:
                artifact_rows = self._build_artifact_rows(rows)
                recipe = {
                    "sql": sql,
                    "positive_terms": list(positive_terms),
                    "snippet_around": snippet_around or "",
                }
                saved_cohort_id = await _write_search_artifact(
                    recipe=recipe,
                    rows=artifact_rows,
                    user_id=user_id,
                    chat_id=__chat_id__,
                )
            except Exception:
                log.exception("Failed to persist Scout cohort")

            if saved_cohort_id:
                asyncio.create_task(
                    _prune_artifacts(
                        user_id, __chat_id__, self.valves.owui_files_lru_keep
                    )
                )

        await self._emit_status(__event_emitter__, "Query complete", done=True)

        # ------------------------------------------------------------------
        # 6. Build LLM context summary (text columns stripped).
        # ------------------------------------------------------------------
        sample_rows = self._select_sample_rows(rows, display, llm_context_rows)
        llm_rows = [self._strip_text_columns(r) for r in sample_rows]
        summary = self._build_search_summary(
            columns=columns,
            total_rows=len(rows),
            sample_rows=llm_rows,
            negation_applied=negation_applied,
            included_count=included_count,
            excluded_count=excluded_count,
            positive_terms=positive_terms,
            wrapped_patterns=wrapped_patterns,
            saved_cohort_id=saved_cohort_id,
        )

        # ------------------------------------------------------------------
        # 7. Build user-facing iframe. (No detail mode here.)
        # ------------------------------------------------------------------
        rich_ui = self._build_search_ui(columns, rows, display, saved_cohort_id)
        if rich_ui is None:
            return summary
        return (
            HTMLResponse(content=rich_ui, headers={"Content-Disposition": "inline"}),
            summary,
        )

    # ------------------------------------------------------------------
    # read_reports — full text for specific reports
    # ------------------------------------------------------------------
    async def read_reports(
        self,
        message_control_ids: Optional[list[str]] = None,
        accession_numbers: Optional[list[str]] = None,
        cohort_id: Optional[str] = None,
        sections: Optional[list[str]] = None,
        display: str = "detail",
        max_reports: int = 5,
        sample: str = "first",
        included: Optional[bool] = None,
        __user__: Optional[dict] = None,
        __event_emitter__: Optional[Callable[[Any], Awaitable[None]]] = None,
        __chat_id__: str = "",
    ) -> Any:
        """
        Return full report sections for specific reports or sampled from a
        saved cohort. Three equally first-class entry paths.

        Use this tool to FETCH the actual narrative text of reports (FINDINGS,
        IMPRESSION, ADDENDUM). Use `search_reports` to find the rows in the
        first place; `read_reports` is the read-side partner. After a
        `search_reports` cohort is saved, this tool's `cohort_id` +
        `included` + `sample` parameters are the natural way to spot-check
        the assertion classifier.

        IMPORTANT — what lands in your context vs. what the user sees:
        Only the FIRST report's full narrative goes into your context. Every
        other row is a one-line identifier with the cohort's why-excerpt
        (the assertion classifier's snippet). The user sees ALL fetched
        reports as cards in the iframe — they can scroll. If you need to
        read another specific report's text, re-call read_reports with that
        `message_control_id` directly. This keeps your context focused on
        one narrative at a time.

        :param message_control_ids: Read exactly these report versions (HL7
            MSH-10 primary key). The right call when you care about
            preliminary vs. final reads.
        :param accession_numbers: Read the latest message per accession.
            Natural shape when a researcher names accessions and doesn't care
            about prior versions.
        :param cohort_id: Sample from a saved cohort (`coh_<6 chars>`,
            from a previous `search_reports` or `load_id_list`). Combine with
            `max_reports`, `sample`, and optional `included` (True = included
            only, False = excluded only, None = a balanced mix) — useful for
            spot checks like "3 random included + 3 random excluded".
        :param sections: Which report_section_* columns to fetch. Default
            ["findings", "impression"]. Pass ["addendum", "technician_note",
            "findings", "impression"] to get everything.
        :param display: One of "detail" (cards with full text — default),
            "table" (comparative table when scanning multiple impressions
            side-by-side), "none" (no iframe).
        :param max_reports: Cap on rows fetched (and rendered as cards in
            the iframe). Default 5. The LLM-context cap is independent — only
            the first report's narrative goes into your context regardless
            of `max_reports`.
        :param sample: How to pick when `cohort_id` supplies more than
            max_reports rows. "first" or "random". Default "first".
        :param included: Only meaningful with `cohort_id`. True = included
            rows only, False = excluded only, None (default) = a balanced
            mix of both groups when present (max_reports/2 from each side).
            Use None when the user asks for "both positive and negative
            samples" or "verify the cohort" — a single call instead of two.
        :return: Summary text + iframe. First report's full text goes to
            your context; remaining rows are identifier + classifier excerpt.
        """
        if __user__ is None:
            __user__ = {}
        if sections is None:
            sections = ["findings", "impression"]
        if display not in VALID_READ_DISPLAYS:
            display = "detail"

        # Resolve target IDs.
        target_column: str
        target_ids: list[str]
        included_lookup: dict[str, Any]
        artifact_search_patterns: list[str]
        try:
            (target_column, target_ids, included_lookup, artifact_search_patterns) = (
                await self._resolve_target_ids(
                    message_control_ids=message_control_ids,
                    accession_numbers=accession_numbers,
                    cohort_id=cohort_id,
                    included=included,
                    max_reports=max_reports,
                    sample=sample,
                )
            )
        except ValueError as exc:
            return f"Error: {exc}"

        if not target_ids:
            return "No reports matched the requested criteria."

        await self._emit_status(__event_emitter__, "Fetching report text…", done=False)

        # Build SELECT — section columns + identity + a few useful metadata
        # cols for the card header. Include `report_text` always as a fallback:
        # report_section_* fields are derived from HL7 OBX-3.1.2 suffix matching
        # (GDT/IMP/ADT) and many message types — Radiology Events, NM/PET/some
        # CT reports — don't carry those suffixes so the parsed sections are
        # empty even when raw text exists.
        section_columns = [
            f"report_section_{s.strip()}" for s in sections if s and s.strip()
        ]
        context_columns = [
            "message_control_id",
            "accession_number",
            "epic_mrn",
            "modality",
            "service_name",
            "message_dt",
            "patient_age",
            "sex",
            "report_text",
        ]
        select_cols = list(dict.fromkeys(context_columns + section_columns))

        select_clause = ", ".join(select_cols)
        in_clause = _render_in_clause(target_column, [str(v) for v in target_ids])
        # When fetching by accession_number, dedup to the latest message via reports_latest.
        table = "delta.default.reports_latest"
        sql = f"SELECT {select_clause} FROM {table} WHERE {in_clause}"

        try:
            rows, columns = await self._execute_trino_query(sql)
        except Exception as exc:
            log.exception("read_reports Trino query failed")
            await self._emit_status(
                __event_emitter__, f"Fetch failed: {exc}", done=True
            )
            return f"Error fetching reports: {exc}"

        await self._emit_status(__event_emitter__, "Fetch complete", done=True)

        if not rows:
            return f"No reports found in {table} for the requested IDs."

        # Re-attach the artifact's `included` flag + why excerpts to each
        # fetched row so the detail cards AND the LLM-bound summary tag rows
        # as INCLUDED / EXCLUDED with the assertion-classifier rationale.
        if included_lookup:
            for row in rows:
                key = str(row.get(target_column))
                if key in included_lookup:
                    entry = included_lookup[key]
                    if isinstance(entry, dict):
                        row["included"] = entry.get("included")
                        if entry.get("why_included"):
                            row["why_included"] = entry["why_included"]
                        if entry.get("why_excluded"):
                            row["why_excluded"] = entry["why_excluded"]
                    else:
                        row["included"] = bool(entry)

        # Reorder rows so positives come first, then negatives — easier for
        # the user to scan side-by-side when both groups are present.
        if included_lookup:
            rows.sort(
                key=lambda r: (r.get("included") is False, r.get("included") is None)
            )

        summary = self._build_read_summary(rows, columns, sections)
        rich_ui = self._build_read_ui(columns, rows, display, artifact_search_patterns)
        if rich_ui is None:
            return summary
        return (
            HTMLResponse(content=rich_ui, headers={"Content-Disposition": "inline"}),
            summary,
        )

    # ------------------------------------------------------------------
    # ID resolution for read_reports
    # ------------------------------------------------------------------
    async def _resolve_target_ids(
        self,
        message_control_ids: Optional[list[str]],
        accession_numbers: Optional[list[str]],
        cohort_id: Optional[str],
        included: Optional[bool],
        max_reports: int,
        sample: str,
    ) -> tuple[str, list[str], dict[str, Any], list[str]]:
        """Returns (column, target_ids, included_lookup, search_patterns).
        search_patterns comes from the saved cohort's recipe (so cards can
        highlight matches) and is empty for explicit-id paths."""
        provided = [x for x in (message_control_ids, accession_numbers, cohort_id) if x]
        if len(provided) != 1:
            raise ValueError(
                "Pass exactly one of: message_control_ids, accession_numbers, "
                "or cohort_id."
            )

        if message_control_ids:
            return "message_control_id", list(message_control_ids)[:max_reports], {}, []
        if accession_numbers:
            return "accession_number", list(accession_numbers)[:max_reports], {}, []

        # cohort_id path
        artifact = await _read_artifact(cohort_id)
        if artifact is None:
            raise ValueError(f"cohort {cohort_id!r} not found")
        all_rows = artifact.get("rows") or []
        column = _pick_substitution_column(all_rows)
        if not column:
            raise ValueError("saved cohort has no recognizable identifier column")
        # Pull positive_terms from the recipe so cards can highlight matches.
        recipe = artifact.get("recipe") or {}
        search_patterns = list(recipe.get("positive_terms") or [])

        if included is True:
            picked = [r for r in all_rows if r.get("included")]
        elif included is False:
            picked = [r for r in all_rows if not r.get("included")]
        else:
            # included=None: if the artifact has both flagged groups, split the
            # sample evenly so the user gets a mixed positive+negative spot
            # check from a single call. Otherwise just take everything.
            inc = [r for r in all_rows if r.get("included")]
            exc = [r for r in all_rows if "included" in r and not r.get("included")]
            if inc and exc:
                half = max(1, max_reports // 2)
                if sample == "random":
                    inc_pick = random.sample(inc, min(half, len(inc)))
                    exc_pick = random.sample(
                        exc, min(max_reports - len(inc_pick), len(exc))
                    )
                else:
                    inc_pick = inc[:half]
                    exc_pick = exc[: max_reports - len(inc_pick)]
                picked = inc_pick + exc_pick
                ids = [str(r[column]) for r in picked if r.get(column) is not None]
                lookup = {
                    str(r[column]): {
                        "included": bool(r.get("included")),
                        "why_included": r.get("why_included") or "",
                        "why_excluded": r.get("why_excluded") or "",
                    }
                    for r in picked
                    if r.get(column) is not None
                }
                return column, ids, lookup, search_patterns
            picked = all_rows

        ids = [str(r[column]) for r in picked if r.get(column) is not None]
        if not ids:
            return column, [], {}, search_patterns
        if sample == "random" and len(ids) > max_reports:
            ids = random.sample(ids, max_reports)
        else:
            ids = ids[:max_reports]
        lookup = {
            str(r[column]): {
                "included": bool(r.get("included")),
                "why_included": r.get("why_included") or "",
                "why_excluded": r.get("why_excluded") or "",
            }
            for r in picked
            if r.get(column) is not None and "included" in r
        }
        return column, ids, lookup, search_patterns

    # ------------------------------------------------------------------
    # Trino client (REST API via httpx — no external `trino` package)
    # ------------------------------------------------------------------
    async def _execute_trino_query(self, sql: str) -> tuple[list[dict], list[str]]:
        import httpx

        base = f"http://{self.valves.trino_host}:{self.valves.trino_port}"
        headers = {
            "X-Trino-User": self.valves.trino_user,
            "X-Trino-Catalog": self.valves.trino_catalog,
            "X-Trino-Schema": self.valves.trino_schema,
            "Content-Type": "text/plain",
        }
        columns: list[str] = []
        rows: list[dict] = []

        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
            resp = await client.post(
                f"{base}/v1/statement", content=sql, headers=headers
            )
            resp.raise_for_status()
            data = resp.json()
            while True:
                if not columns and "columns" in data:
                    columns = [c["name"] for c in data["columns"]]
                chunk = data.get("data") or []
                for raw_row in chunk:
                    rows.append({columns[i]: raw_row[i] for i in range(len(columns))})
                state = (data.get("stats") or {}).get("state")
                error = data.get("error")
                if error:
                    msg = error.get("message", "Trino error")
                    raise RuntimeError(f"{msg} (state={state})")
                next_uri = data.get("nextUri")
                if not next_uri:
                    break
                resp = await client.get(next_uri, headers=headers)
                resp.raise_for_status()
                data = resp.json()

        return rows, columns

    # ------------------------------------------------------------------
    # Helpers — artifact rows, summary builders, sample selection
    # ------------------------------------------------------------------
    @staticmethod
    def _build_artifact_rows(rows: list[dict]) -> list[dict]:
        """Project search result rows down to the artifact-row shape: identifier
        columns + included/why_excluded flags. Drops everything else (the LLM
        already saw it; rerunning the query rederives anything else needed)."""
        out: list[dict] = []
        for row in rows:
            entry: dict[str, Any] = {}
            for key in ARTIFACT_ROW_KEYS:
                if key in row and row[key] is not None and row[key] != "":
                    entry[key] = row[key]
            if entry:
                out.append(entry)
        return out

    @staticmethod
    def _strip_text_columns(row: dict) -> dict:
        """Return a copy of `row` with the giant text columns removed. Snippet
        windows (`snippets`) are kept — they're bounded."""
        return {k: v for k, v in row.items() if k not in TEXT_COLUMNS_TO_STRIP}

    def _select_sample_rows(
        self,
        rows: list[dict],
        display: str,
        llm_context_rows: Optional[int],
    ) -> list[dict]:
        if llm_context_rows is None:
            llm_context_rows = DISPLAY_LLM_ROW_DEFAULTS.get(display)
        n = len(rows) if llm_context_rows is None else min(llm_context_rows, len(rows))
        n = min(n, self.valves.safety_max_context_rows)
        return rows[:n]

    def _build_search_summary(
        self,
        columns: list[str],
        total_rows: int,
        sample_rows: list[dict],
        negation_applied: bool,
        included_count: int,
        excluded_count: int,
        positive_terms: list[str],
        saved_cohort_id: str,
        wrapped_patterns: Optional[list[tuple[str, str]]] = None,
    ) -> str:
        parts: list[str] = []

        # Lead with the cohort_id when one was saved — it's the most useful
        # piece of information for chaining the next call.
        if saved_cohort_id:
            parts.append(f"✓ Saved cohort: cohort_id={saved_cohort_id}")
            parts.append(
                f"  ({total_rows} row{'s' if total_rows != 1 else ''}; "
                + (
                    f"{included_count} included, {excluded_count} excluded by "
                    f"assertion classifier"
                    if negation_applied
                    else "no assertion classifier applied"
                )
                + ")"
            )
            parts.append("")
        else:
            parts.append(
                f"Query returned {total_rows} row{'s' if total_rows != 1 else ''} "
                f"across {len(columns)} columns. (No cohort saved — include "
                f"message_control_id and/or accession_number in the SELECT to "
                f"make the result chainable / exportable.)"
            )
            parts.append("")

        parts.append(f"Columns: {', '.join(columns)}")

        if negation_applied:
            parts.append(
                f"Assertion classifier ran on {positive_terms!r} → "
                f"{included_count} included, {excluded_count} excluded."
            )
            if wrapped_patterns:
                pairs = ", ".join(
                    f"{orig!r}→{norm!r}" for orig, norm in wrapped_patterns
                )
                parts.append(
                    f"Note: short bare terms auto-wrapped in word boundaries "
                    f"({pairs}); pass a regex with metacharacters to opt out."
                )

        if sample_rows:
            parts.append("")
            parts.append(f"Sample ({len(sample_rows)} of {total_rows} rows):")
            visible_cols = [c for c in columns if c not in TEXT_COLUMNS_TO_STRIP]
            # Surface annotations the algorithm attached. `why_included` lets
            # the reader spot false positives at a glance — if it shows
            # "...the procedure was performed..." you know "PE" matched
            # "performed", not pulmonary embolism.
            for extra in ("snippets", "why_included", "why_excluded"):
                if any(extra in r for r in sample_rows) and extra not in visible_cols:
                    visible_cols.append(extra)
            header = " | ".join(visible_cols)
            sep = "-+-".join("-" * len(c) for c in visible_cols)
            body_lines = [
                " | ".join(str(row.get(c, ""))[:160] for c in visible_cols)
                for row in sample_rows
            ]
            parts.append(header)
            parts.append(sep)
            parts.extend(body_lines)
            if total_rows > len(sample_rows):
                parts.append(
                    f"\n... and {total_rows - len(sample_rows)} more rows (not in your "
                    f"context — refer to the rendered display, do not enumerate)."
                )

        if saved_cohort_id:
            parts.append("")
            parts.append(
                f"To verify the cohort, call read_reports(cohort_id="
                f"{saved_cohort_id!r}, included=True/False, sample='random'). "
                f"To export, point the user to the Send-to-XNAT button."
            )
        return "\n".join(parts)

    @staticmethod
    def _build_read_summary(
        rows: list[dict], columns: list[str], sections: list[str]
    ) -> str:
        """LLM-bound summary that puts FULL text for only the first
        MAX_LLM_READ_FULL_TEXT_REPORTS reports into your context. Every other
        row is a one-line identifier plus the cohort's `why_included` /
        `why_excluded` excerpt (already on the artifact, free). The iframe
        still renders all rows as cards — this only bounds your context so
        you can summarize without drowning in narrative.

        Why: a focused LLM reads ONE report carefully and reasons about the
        cohort via the assertion-classifier excerpts. Multiple full reports
        in context invites cross-referencing errors and inflates token cost
        with diminishing returns."""
        n = len(rows)
        section_columns = [
            f"report_section_{s.strip()}" for s in sections if s and s.strip()
        ]

        def _row_header(row: dict, idx: int, prefix: str = "===") -> str:
            mci = row.get("message_control_id") or row.get("accession_number") or "?"
            bits = [str(mci)]
            modality = row.get("modality", "") or ""
            service = (row.get("service_name", "") or "").strip()
            age = row.get("patient_age", "")
            sex = row.get("sex", "") or ""
            dt = row.get("message_dt", "") or ""
            inc = row.get("included")
            if modality:
                bits.append(str(modality))
            if service:
                bits.append(service[:60])
            if dt:
                bits.append(str(dt)[:10])  # date portion only
            if age:
                bits.append(f"age {age}")
            if sex:
                bits.append(str(sex))
            if inc is False:
                bits.append("EXCLUDED")
            elif inc is True:
                bits.append("INCLUDED")
            return f"{prefix} Report {idx}: {' · '.join(bits)} {prefix}"

        full_text_count = min(MAX_LLM_READ_FULL_TEXT_REPORTS, n)
        parts: list[str] = [
            f"Fetched {n} report{'s' if n != 1 else ''} "
            f"(sections: {', '.join(sections)}). "
            + (
                f"Full FINDINGS / IMPRESSION text for the first "
                f"{full_text_count} report{'s' if full_text_count != 1 else ''} "
                f"is in your context below. Every other row is a one-line "
                f"identifier with the assertion classifier's why-excerpt — "
                f"the iframe shows all reports as cards. To read another "
                f"report's text, re-call read_reports with that "
                f"`message_control_id` directly."
                if n > full_text_count
                else "Full text follows; the user already sees the rendered cards."
            )
        ]

        # Phase 1 — full text for the first N reports.
        for i, row in enumerate(rows[:full_text_count], start=1):
            parts.append("")
            parts.append(_row_header(row, i))
            wrote_section = False
            for col in section_columns:
                val = row.get(col)
                if val:
                    parts.append(f"[{col.removeprefix('report_section_').upper()}]")
                    parts.append(str(val))
                    wrote_section = True
            if not wrote_section:
                raw = row.get("report_text")
                if raw:
                    parts.append("[REPORT_TEXT — parsed sections empty for this row]")
                    parts.append(str(raw))
                else:
                    parts.append("(no findings/impression/report_text on this row)")

        # Phase 2 — one-line identifier + why excerpt for the rest.
        if n > full_text_count:
            parts.append("")
            parts.append(
                f"--- Remaining {n - full_text_count} report"
                f"{'s' if (n - full_text_count) != 1 else ''} "
                f"(identifier + classifier excerpt only) ---"
            )
            for i, row in enumerate(rows[full_text_count:], start=full_text_count + 1):
                parts.append(_row_header(row, i, prefix="·"))
                why_inc = (row.get("why_included") or "").strip()
                why_exc = (row.get("why_excluded") or "").strip()
                if why_inc:
                    parts.append(f"  ↳ included: {why_inc[:200]}")
                elif why_exc:
                    parts.append(f"  ↳ excluded: {why_exc[:200]}")
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # User-facing render
    # ------------------------------------------------------------------
    def _build_search_ui(
        self,
        columns: list[str],
        rows: list[dict],
        display: str,
        saved_cohort_id: str = "",
    ) -> Optional[str]:
        if display == "none":
            return None
        body = self._render_table(columns, rows, with_text_cols=False)

        n_rows = len(rows)
        # Subtle cohort_id chip — visible enough that the user notices it
        # before clicking Send to XNAT, quiet enough not to dominate the UI.
        cohort_chip = (
            f'<span class="cohort-chip" title="Saved cohort handle — pass to '
            f"search_reports(cohort_id=...) or read_reports(cohort_id=...) "
            f'to chain another query">'
            f"cohort: <code>{html_module.escape(saved_cohort_id)}</code>"
            f"</span>"
            if saved_cohort_id
            else ""
        )
        toolbar = (
            f'<div class="toolbar">'
            f'<span class="info">{n_rows} row{"s" if n_rows != 1 else ""}, '
            f"{len(columns)} columns</span>"
            f"{cohort_chip}"
            f"</div>"
        )
        return self._wrap_html(toolbar + f'<div class="body">{body}</div>')

    def _build_read_ui(
        self,
        columns: list[str],
        rows: list[dict],
        display: str,
        search_patterns: Optional[list[str]] = None,
    ) -> Optional[str]:
        if display == "none":
            return None
        if display == "table":
            body = self._render_table(columns, rows, with_text_cols=False)
        else:  # detail
            body = self._render_detail(columns, rows, search_patterns or [])
        n_rows = len(rows)
        legend = ""
        if search_patterns:
            terms = ", ".join(html_module.escape(p) for p in search_patterns[:5])
            legend = (
                f'<span class="legend">'
                f'<span class="hl-pos">positive</span> '
                f'<span class="hl-neg">negated</span> '
                f"matches for: <code>{terms}</code>"
                f"</span>"
            )
        toolbar = (
            f'<div class="toolbar">'
            f'<span class="info">{n_rows} report{"s" if n_rows != 1 else ""}</span>'
            f"{legend}"
            f"</div>"
        )
        return self._wrap_html(toolbar + f'<div class="body">{body}</div>')

    def _render_detail(
        self,
        columns: list[str],
        rows: list[dict],
        search_patterns: Optional[list[str]] = None,
    ) -> str:
        """Cards-style render with report section text. Used by read_reports.

        Both the card count and per-section length are capped — without these,
        a 20-report fetch with 5 KB sections produces 100 KB+ of HTML that
        gets stored verbatim in chat history. The full text is still in the
        LLM tool-result summary; users who want unabridged copies can use
        Download CSV from a tighter `max_reports`."""
        if not rows:
            return '<div class="empty">No results.</div>'
        n_total = len(rows)
        rendered_rows = rows[:MAX_RENDERED_DETAIL_CARDS]
        truncated_count = n_total - len(rendered_rows)
        cards = []
        for row in rendered_rows:
            header_bits = []
            for c in (
                "message_control_id",
                "accession_number",
                "patient_age",
                "sex",
                "modality",
                "service_name",
                "message_dt",
            ):
                if c in row and row[c] not in (None, ""):
                    header_bits.append(
                        f'<span class="card-meta"><b>{html_module.escape(c)}:</b> '
                        f"{html_module.escape(str(row[c]))}</span>"
                    )
            # Prefer parsed report_section_* columns when any have content;
            # fall back to raw `report_text` only when no parsed sections exist
            # for this row (avoids rendering the same content twice).
            section_vals = [
                (c, row.get(c))
                for c in columns
                if c.startswith("report_section_") and row.get(c)
            ]
            patterns = search_patterns or []

            def _prep(text: str) -> tuple[str, bool]:
                """Clip + (optionally) highlight. Returns HTML-safe body string
                and whether it was clipped."""
                s = str(text)
                truncated = len(s) > MAX_DETAIL_SECTION_CHARS
                if truncated:
                    s = s[:MAX_DETAIL_SECTION_CHARS].rstrip() + "…"
                body_html = (
                    _highlight_text(s, patterns) if patterns else html_module.escape(s)
                )
                return body_html, truncated

            sections = []
            if section_vals:
                for c, val in section_vals:
                    body_html, was_clipped = _prep(val)
                    suffix = (
                        '<div class="card-section-clip">[truncated for display]</div>'
                        if was_clipped
                        else ""
                    )
                    sections.append(
                        f'<div class="card-section">'
                        f'<div class="card-section-title">{html_module.escape(c)}</div>'
                        f'<div class="card-section-body">{body_html}</div>'
                        f"{suffix}"
                        f"</div>"
                    )
            elif row.get("report_text"):
                body_html, was_clipped = _prep(row["report_text"])
                suffix = (
                    '<div class="card-section-clip">[truncated for display]</div>'
                    if was_clipped
                    else ""
                )
                sections.append(
                    f'<div class="card-section">'
                    f'<div class="card-section-title">report_text (parsed sections empty)</div>'
                    f'<div class="card-section-body">{body_html}</div>'
                    f"{suffix}"
                    f"</div>"
                )
            inclusion = ""
            if "included" in row:
                inc = bool(row.get("included"))
                cls = "tag-included" if inc else "tag-excluded"
                lbl = "INCLUDED" if inc else "EXCLUDED"
                inclusion = f'<span class="tag {cls}">{lbl}</span>'
            cards.append(
                f'<div class="card">'
                f'<div class="card-header">{inclusion}{"".join(header_bits)}</div>'
                f'{"".join(sections)}'
                f"</div>"
            )
        truncated_footer = (
            f'<div class="table-truncated-note">'
            f"Showing first {len(rendered_rows)} of {n_total} reports. "
            f"Re-call read_reports with a tighter set if you need more cards."
            f"</div>"
            if truncated_count > 0
            else ""
        )
        # Click-through pager: only one card visible at a time, prev/next
        # buttons + counter. Way easier to scan than a long scrolling stack,
        # and keeps the iframe height bounded to one card's worth.
        wrapped_cards = "".join(
            f'<div class="card-page" data-idx="{i}"{"" if i == 0 else " hidden"}>{c}</div>'
            for i, c in enumerate(cards)
        )
        n = len(cards)
        pager = f"""
            <div class="pager-toolbar">
                <button class="pager-btn" id="pager-prev" onclick="pagerStep(-1)" disabled>&larr; Prev</button>
                <span class="pager-counter"><span id="pager-idx">1</span> / {n}</span>
                <button class="pager-btn" id="pager-next" onclick="pagerStep(1)"{(' disabled' if n <= 1 else '')}>Next &rarr;</button>
            </div>
        """
        pager_script = f"""
            <script>
            (function() {{
                const total = {n};
                let idx = 0;
                window.pagerStep = function(delta) {{
                    const next = Math.max(0, Math.min(total - 1, idx + delta));
                    if (next === idx) return;
                    document.querySelector('.card-page[data-idx="' + idx + '"]').setAttribute('hidden', '');
                    document.querySelector('.card-page[data-idx="' + next + '"]').removeAttribute('hidden');
                    idx = next;
                    document.getElementById('pager-idx').textContent = (idx + 1);
                    document.getElementById('pager-prev').disabled = (idx === 0);
                    document.getElementById('pager-next').disabled = (idx >= total - 1);
                    parent.postMessage({{ type: 'iframe:height', height: Math.max(750, document.documentElement.scrollHeight) }}, '*');
                }};
            }})();
            </script>
        """
        return f'<div class="cards-pager">{pager}{wrapped_cards}</div>{pager_script}{truncated_footer}'

    def _render_table(
        self, columns: list[str], rows: list[dict], with_text_cols: bool = True
    ) -> str:
        visible_cols = (
            list(columns)
            if with_text_cols
            else [c for c in columns if c not in TEXT_COLUMNS_TO_STRIP]
        )
        th = "".join(f"<th>{html_module.escape(c)}</th>" for c in visible_cols)
        # Sort included rows first so the user sees the cohort hits without
        # scrolling. Rows without an `included` field (no negation applied)
        # all sort equally; preserve original ordering within each group.
        sortable = "included" in {k for r in rows for k in r.keys()}
        if sortable:
            ordered_rows = sorted(
                enumerate(rows),
                key=lambda pair: (pair[1].get("included") is False, pair[0]),
            )
            ordered_rows = [r for _, r in ordered_rows]
        else:
            ordered_rows = list(rows)
        # Cap rendered rows; the rest is in the CSV download. Per-cell text is
        # also clipped so a stray paragraph in a cell doesn't blow up payload.
        n_total = len(ordered_rows)
        rendered = ordered_rows[:MAX_RENDERED_TABLE_ROWS]
        truncated = n_total > len(rendered)
        per_cell_cap = 240
        tr_rows = []
        for row in rendered:
            tds = []
            for c in visible_cols:
                val = str(row.get(c, "") or "")
                if len(val) > per_cell_cap:
                    val = val[:per_cell_cap].rstrip() + "…"
                tds.append(f"<td>{html_module.escape(val)}</td>")
            # Tint included rows light green, excluded light red. Rows without
            # `included` (e.g. aggregate breakdowns) get no tint.
            row_class = ""
            if "included" in row:
                row_class = (
                    ' class="row-included"'
                    if row.get("included")
                    else ' class="row-excluded"'
                )
            tr_rows.append(f"<tr{row_class}>{''.join(tds)}</tr>")
        body = (
            '<div class="table-wrap">'
            f"<table><thead><tr>{th}</tr></thead><tbody>{''.join(tr_rows)}</tbody></table>"
            "</div>"
        )
        if truncated:
            body += (
                f'<div class="table-truncated-note">'
                f"Showing first {len(rendered)} of {n_total} rows. "
                f"Re-run search_reports with a tighter filter if you need to see more."
                f"</div>"
            )
        return body

    def _wrap_html(self, body: str) -> str:
        return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    html, body {{ min-height: 750px; }}
    body {{
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        font-size: 13px;
        color: #e0e0e0;
        background: #1e1e1e;
    }}
    .toolbar {{
        display: flex; align-items: center; justify-content: space-between;
        padding: 8px 12px; background: #2a2a2a; border-bottom: 1px solid #3a3a3a;
        position: sticky; top: 0; z-index: 10;
    }}
    .toolbar .info {{ color: #aaa; }}
    .toolbar .actions {{ display: flex; gap: 6px; }}
    .cohort-chip {{
        font-size: 11px; color: #93c5fd; background: #1e3a8a33;
        padding: 2px 8px; border-radius: 3px; border: 1px solid #1e3a8a66;
        margin-left: auto; margin-right: 8px; cursor: help;
    }}
    .cohort-chip code {{
        background: transparent; color: inherit; font-family: ui-monospace, monospace;
    }}
    .btn {{
        background: #3a3a3a; color: #e0e0e0; border: 1px solid #555;
        padding: 4px 12px; border-radius: 4px; cursor: pointer; font-size: 12px;
    }}
    .btn:hover {{ background: #4a4a4a; }}
    .table-wrap {{ overflow: auto; max-height: 800px; }}
    table {{ border-collapse: collapse; width: 100%; white-space: nowrap; }}
    th {{ background: #2a2a2a; color: #ccc; font-weight: 600; position: sticky; top: 0; z-index: 5; }}
    th, td {{ border: 1px solid #3a3a3a; padding: 5px 10px; text-align: left; }}
    tr:hover {{ background: #2a2a2a; }}
    td {{ max-width: 300px; overflow: hidden; text-overflow: ellipsis; }}
    /* Light tint to distinguish cohort hits (included) from filter-out (excluded). */
    tr.row-included {{ background: #14532d22; }}
    tr.row-included:hover {{ background: #14532d44; }}
    tr.row-excluded {{ background: #4c051922; color: #aaa; }}
    tr.row-excluded:hover {{ background: #4c051944; }}
    tr.row-included td:first-child::before {{
        content: "✓"; color: #86efac; margin-right: 6px; font-weight: 700;
    }}
    tr.row-excluded td:first-child::before {{
        content: "✗"; color: #fda4af; margin-right: 6px; font-weight: 700;
    }}
    .cards {{ padding: 12px; display: flex; flex-direction: column; gap: 12px; }}
    .card {{ background: #2a2a2a; border-radius: 6px; padding: 12px; }}
    .card-header {{ display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 8px; align-items: center; }}
    .card-meta {{ color: #ccc; font-size: 12px; }}
    .card-section {{ margin-top: 8px; }}
    .card-section-title {{ color: #888; font-size: 11px; text-transform: uppercase; letter-spacing: 0.04em; }}
    .card-section-body {{ color: #e0e0e0; white-space: pre-wrap; font-size: 13px; line-height: 1.4; margin-top: 2px; }}
    .tag {{ font-size: 10px; padding: 2px 6px; border-radius: 3px; font-weight: 600; }}
    .tag-included {{ background: #14532d; color: #86efac; }}
    .tag-excluded {{ background: #4c0519; color: #fda4af; }}
    .hl-pos {{ background: #14532d; color: #86efac; padding: 0 3px; border-radius: 2px; font-weight: 600; }}
    .hl-neg {{ background: #4c0519; color: #fda4af; padding: 0 3px; border-radius: 2px; font-weight: 600; }}
    .legend {{ color: #aaa; font-size: 11px; }}
    .legend code {{ background: #1e1e1e; padding: 1px 4px; border-radius: 3px; color: #ccc; }}
    .pager-toolbar {{ display: flex; align-items: center; justify-content: center; gap: 16px; padding: 10px 12px; background: #2a2a2a; border-bottom: 1px solid #3a3a3a; position: sticky; top: 36px; z-index: 9; }}
    .pager-btn {{ background: #3a3a3a; color: #e0e0e0; border: 1px solid #555; padding: 6px 14px; border-radius: 4px; cursor: pointer; font-size: 13px; }}
    .pager-btn:hover:not([disabled]) {{ background: #4a4a4a; }}
    .pager-btn[disabled] {{ opacity: 0.4; cursor: not-allowed; }}
    .pager-counter {{ color: #ccc; font-variant-numeric: tabular-nums; min-width: 60px; text-align: center; }}
    .card-page[hidden] {{ display: none !important; }}
    .empty {{ padding: 32px; text-align: center; color: #888; }}
    .table-truncated-note {{ padding: 8px 12px; color: #aaa; font-size: 11px; font-style: italic; border-top: 1px solid #3a3a3a; background: #2a2a2a; }}
    .card-section-clip {{ color: #888; font-size: 10px; font-style: italic; margin-top: 4px; }}
</style>
</head>
<body>
{body}
<script>
    function reportHeight() {{
        // Use the larger of body's computed scrollHeight and our 750px floor.
        // OWUI's iframe sizer falls back to ~150px when it doesn't get a
        // height it likes; setting a comfortable minimum keeps the rendered
        // UI usable even if a single postMessage gets dropped.
        const h = Math.max(750, document.documentElement.scrollHeight);
        parent.postMessage({{ type: "iframe:height", height: h }}, "*");
    }}
    // Fire several times to defeat any race against OWUI's iframe-sizing setup.
    reportHeight();
    setTimeout(reportHeight, 100);
    setTimeout(reportHeight, 500);
    setTimeout(reportHeight, 1500);
    new MutationObserver(reportHeight).observe(document.body, {{ childList: true, subtree: true }});
    window.addEventListener("load", reportHeight);
    document.addEventListener("toggle", reportHeight, true);
</script>
</body>
</html>"""

    @staticmethod
    async def _emit_status(
        emitter: Optional[Callable], description: str, done: bool
    ) -> None:
        if emitter:
            await emitter(
                {"type": "status", "data": {"description": description, "done": done}}
            )
