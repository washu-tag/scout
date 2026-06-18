"""Assertion classifier + snippet extractor + sample-row picker.

Ported from helm/open-webui-bootstrap/files/payloads/scout_query_tool.py
(the search-building POC). Lives in the service so the OWUI tool stays
thin and the same logic is reusable by other Scout surfaces (notebooks,
batch jobs, future patient-search tooling).

Three responsibilities:

  1. **Negation / uncertainty / historical classification** — per-row
     evidence label (positive | flagged) + plain-English `reason`.
     Sentence-scoped regex, section-stripping, termination cues.
  2. **Snippet extraction** — ±N-sentence windows around regex matches,
     so the LLM sees the keyword in its surrounding context without the
     full report text.
  3. **Sample selection** — pick which rows go into the LLM-bound
     summary, biased to balance confirmed + flagged when both exist.

Bonus: `build_search_summary()` renders the LLM-bound markdown the POC
shipped — count + breakdown + the anti-restatement directive + sample
table + the internal search-handle note.
"""

from __future__ import annotations

import re
from typing import Any, Optional


# --------------------------------------------------------------------------
# Pattern banks. Mirror analytics/notebooks/search/search_builder.py —
# keep both in sync when extending.
# --------------------------------------------------------------------------

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
    r"\brule\s+out\b",
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

# Closes leading negation scope mid-sentence. "no focal mass, but X" → X
# is treated as a positive finding.
TERMINATION_PATTERNS = [
    r"\bbut\b",
    r"\bhowever\b",
    r"\bexcept\b",
    r"\balthough\b",
    r"\baside\s+from\b",
    r"\bother\s+than\b",
]

# Sections that describe WHY the study was done — strip before scanning.
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

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")
_SECTION_HEADER_RE = re.compile(
    r"^\s*(" + "|".join(re.escape(h) for h in _ALL_SECTION_HEADERS) + r")\s*:",
    re.IGNORECASE | re.MULTILINE,
)
# Used by snippet extraction — more permissive sentence splitter that
# also avoids breaking on common medical abbreviations (Dr., 1.5 cm).
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])(?<![A-Z][a-z]\.)\s+")

_SHORT_TOKEN_PATTERN = re.compile(r"^[A-Za-z]{1,4}$")
_REGEX_META_CHARS = set(r"\^$.|?*+()[]{}")


# --------------------------------------------------------------------------
# Labels + columns the LLM-bound output respects
# --------------------------------------------------------------------------

CONFIRMED_LABELS = ("positive",)
FLAGGED_LABELS = ("flagged",)
ALL_ASSERTION_LABELS = CONFIRMED_LABELS + FLAGGED_LABELS

# Columns dropped from the LLM-bound sample (full report text would blow
# out the context; the user sees it in the viewer iframe).
TEXT_COLUMNS_TO_STRIP = frozenset(
    {
        "report_text",
        "report_section_findings",
        "report_section_impression",
        "report_section_addendum",
        "report_section_technician_note",
    }
)

# Identifier columns that mark a query as row-level (vs aggregate).
PERSISTENCE_TRIGGER_COLUMNS = frozenset(
    {
        "message_control_id",
        "accession_number",
        "epic_mrn",
    }
)


# --------------------------------------------------------------------------
# Pattern normalization
# --------------------------------------------------------------------------


def normalize_search_pattern(pattern: str) -> tuple[str, bool]:
    """Auto-wrap short bare alphabetic tokens (≤4 chars, no metachars)
    in word boundaries. `"PE"` would otherwise match `PET-CT`, `performed`,
    `upper`. Patterns containing regex metacharacters are left alone."""
    if not pattern:
        return pattern, False
    if any(ch in _REGEX_META_CHARS for ch in pattern):
        return pattern, False
    if _SHORT_TOKEN_PATTERN.match(pattern):
        return rf"\b{pattern}\b", True
    return pattern, False


def normalize_search_patterns(
    patterns: list[str],
) -> tuple[list[str], list[tuple[str, str]]]:
    """List variant. Returns (normalized, wrapped_pairs) so the summary
    can surface what we auto-wrapped."""
    out: list[str] = []
    wrapped: list[tuple[str, str]] = []
    for p in patterns:
        norm, was = normalize_search_pattern(p)
        out.append(norm)
        if was:
            wrapped.append((p, norm))
    return out, wrapped


# --------------------------------------------------------------------------
# Section stripping + sentence splitting + per-match classification
# --------------------------------------------------------------------------


def _strip_non_finding_sections(text: str) -> str:
    """Remove HISTORY/INDICATIONS/TECHNIQUE/COMPARISON sections. If no
    section headers found, returns text unchanged. Preserves any leading
    text before the first header (often the report title)."""
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
    parts = _SENTENCE_SPLIT_RE.split(text)
    return [p for p in parts if p.strip()]


def _excerpt(sentence: str, start: int, end: int, pad: int = 60) -> str:
    s = max(0, start - pad)
    e = min(len(sentence), end + pad)
    return f"…{sentence[s:e].strip()}…"


def _classify_match_in_sentence(
    sentence: str, match_start: int, match_end: int
) -> tuple[str, str]:
    """Returns (label, excerpt). Label ∈ {positive, negated, uncertain,
    historical}. Negation cues before OR after the match count. Termination
    cues between leading negation and match close the negation scope."""
    pre = sentence[:match_start]
    post = sentence[match_end:]

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


# --------------------------------------------------------------------------
# ICD code validation (validating_diagnosis_codes)
# --------------------------------------------------------------------------


def _trino_like_to_regex(pattern: str) -> "re.Pattern[str]":
    """`I63%` → `^I63.*$`, `C8_.0` → `^C8..0$`, case-insensitive."""
    escaped = re.escape(pattern).replace(r"\%", ".*").replace(r"\_", ".")
    return re.compile(rf"^{escaped}$", re.IGNORECASE)


def _diagnosis_code(entry: Any) -> str:
    """Trino serializes array<struct> as positional lists, so the code is
    at index 0 of [code, code_text, coding_system]. Fall back to dict."""
    if entry is None:
        return ""
    if isinstance(entry, dict):
        return str(entry.get("diagnosis_code") or "")
    if isinstance(entry, (list, tuple)) and entry:
        first = entry[0]
        return str(first) if first is not None else ""
    return ""


def _row_has_validating_icd(row: dict, code_regexes: list["re.Pattern[str]"]) -> bool:
    if not code_regexes:
        return False
    for d in row.get("diagnoses") or []:
        code = _diagnosis_code(d)
        if code and any(rx.match(code) for rx in code_regexes):
            return True
    return False


# --------------------------------------------------------------------------
# Public API — apply_negation, extract_snippets, select_sample,
# build_search_summary
# --------------------------------------------------------------------------


class ClassifierError(ValueError):
    """Raised when the caller asked for classification but the SELECT
    didn't include the columns the classifier needs."""


def apply_negation(
    rows: list[dict],
    search_patterns: list[str],
    validating_diagnosis_codes: Optional[list[str]] = None,
    text_column: str = "report_text",
) -> tuple[bool, dict[str, int], list[tuple[str, str]]]:
    """Annotate each row with `evidence` + `reason`. Returns
    (was_applied, label_counts, wrapped_pairs).

    Label rules:
      * row's `diagnoses` array matches `validating_diagnosis_codes` →
        `positive` (reason names the ICD code)
      * term in text, ≥1 positive match → `positive` (excerpt as reason)
      * term in text, all matches negated/uncertain/historical →
        `flagged` (reason prefixed [NEGATIVE]/[POSSIBLE]/[HISTORICAL])
      * term not in text → `positive` (SQL matched non-textually;
        classifier abstains)
    """
    counts: dict[str, int] = {label: 0 for label in ALL_ASSERTION_LABELS}

    if not search_patterns:
        for row in rows:
            row["reason"] = ""
        return False, counts, []

    if rows and not any(text_column in r for r in rows):
        raise ClassifierError(
            f"positive_clinical_findings was set but no row contains the "
            f"text column {text_column!r}. Include it in your SELECT."
        )
    if validating_diagnosis_codes and rows and not any("diagnoses" in r for r in rows):
        raise ClassifierError(
            "validating_diagnosis_codes was set but no row contains "
            "`diagnoses`. Include it in your SELECT."
        )

    norm_patterns, wrapped = normalize_search_patterns(search_patterns)
    pattern_re = re.compile(
        "|".join(f"(?:{p})" for p in norm_patterns), re.IGNORECASE | re.DOTALL
    )
    icd_regexes = [
        _trino_like_to_regex(p) for p in (validating_diagnosis_codes or []) if p
    ]

    for row in rows:
        if icd_regexes and _row_has_validating_icd(row, icd_regexes):
            matched_codes = [
                _diagnosis_code(d)
                for d in (row.get("diagnoses") or [])
                if _diagnosis_code(d)
                and any(rx.match(_diagnosis_code(d)) for rx in icd_regexes)
            ]
            row["reason"] = (
                f"ICD code {', '.join(matched_codes)} matches "
                f"{validating_diagnosis_codes!r}"
            )
            row["evidence"] = "positive"
            counts["positive"] += 1
            continue

        text = row.get(text_column) or ""
        if not text:
            row["reason"] = "no report_text — classifier abstained"
            row["evidence"] = "positive"
            counts["positive"] += 1
            continue

        scan_text = _strip_non_finding_sections(text)
        positive_excerpt: Optional[str] = None
        first_disqualified: Optional[tuple[str, str]] = None
        for sent in _split_sentences(scan_text):
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
            row["reason"] = positive_excerpt
            row["evidence"] = "positive"
            counts["positive"] += 1
        elif first_disqualified is not None:
            sublabel, excerpt = first_disqualified
            display = {
                "negated": "NEGATIVE",
                "uncertain": "POSSIBLE",
            }.get(sublabel, sublabel.upper())
            row["reason"] = f"[{display}] {excerpt}"
            row["evidence"] = "flagged"
            counts["flagged"] += 1
        else:
            row["reason"] = (
                "SQL match came from a non-text criterion "
                "(e.g. diagnosis code, service_name, modality, or year)"
            )
            row["evidence"] = "positive"
            counts["positive"] += 1
    return True, counts, wrapped


def extract_snippets(
    text: Optional[str], pattern: str, window_sentences: int = 1
) -> list[str]:
    """±N-sentence windows around each non-overlapping regex match."""
    if not text or not pattern:
        return []
    try:
        compiled = re.compile(pattern, re.IGNORECASE | re.DOTALL)
    except re.error:
        return []

    sentences = _SENTENCE_BOUNDARY.split(text)
    spans: list[tuple[int, int, str]] = []
    cursor = 0
    for sentence in sentences:
        if not sentence:
            continue
        start = text.find(sentence, cursor)
        if start < 0:
            start = cursor
        end = start + len(sentence)
        spans.append((start, end, sentence))
        cursor = end

    out: list[str] = []
    seen: set[tuple[int, int]] = set()
    for match in compiled.finditer(text):
        for idx, (start, end, _) in enumerate(spans):
            if start <= match.start() < end:
                lo = max(0, idx - window_sentences)
                hi = min(len(spans), idx + window_sentences + 1)
                key = (lo, hi)
                if key in seen:
                    break
                seen.add(key)
                window = " ".join(s for _, _, s in spans[lo:hi]).strip()
                if window:
                    out.append(window)
                break
    return out


def strip_text_columns(row: dict) -> dict:
    """Copy of `row` with the big text columns removed. `snippets` is
    bounded so it stays."""
    return {k: v for k, v in row.items() if k not in TEXT_COLUMNS_TO_STRIP}


def select_sample_rows(
    rows: list[dict],
    *,
    llm_context_rows: int,
    safety_cap: int = 200,
) -> list[dict]:
    """Pick the LLM-bound sample. When the classifier produced both
    confirmed and flagged rows, balance the sample across both so the
    model sees the disagreement and can decide whether to keep or filter.
    Otherwise return the first N."""
    n = min(llm_context_rows, len(rows), safety_cap)
    if n < 1:
        return []
    if n >= 2 and any(r.get("evidence") for r in rows):
        confirmed = [r for r in rows if r.get("evidence") in CONFIRMED_LABELS]
        flagged = [r for r in rows if r.get("evidence") in FLAGGED_LABELS]
        if confirmed and flagged:
            conf_quota = max(1, n // 2 + (n % 2))
            flag_quota = n - conf_quota
            return confirmed[:conf_quota] + flagged[:flag_quota]
    return rows[:n]


def build_search_summary(
    *,
    columns: list[str],
    total_rows: int,
    sample_rows: list[dict],
    negation_applied: bool,
    label_counts: dict[str, int],
    saved_search_id: str,
    wrapped_patterns: Optional[list[tuple[str, str]]] = None,
) -> str:
    """Render the LLM-bound markdown the POC shipped:
    - count + classifier breakdown
    - column list + auto-wrap note
    - anti-restatement directive (when the iframe will render)
    - sample table (id column hidden; evidence/snippets/reason appended)
    - internal search handle note
    """
    parts: list[str] = []
    positive_n = label_counts.get("positive", 0)
    flagged_n = label_counts.get("flagged", 0)

    if negation_applied:
        if flagged_n:
            parts.append(
                f"SQL matched {total_rows} row{'s' if total_rows != 1 else ''}: "
                f"{positive_n} positive, {flagged_n} flagged for review "
                f"(text says NEGATIVE / POSSIBLE / HISTORICAL — see each "
                f"row's `reason`)."
            )
        else:
            parts.append(
                f"SQL matched {total_rows} row{'s' if total_rows != 1 else ''}, "
                f"all positive."
            )
    else:
        parts.append(
            f"SQL matched {total_rows} row{'s' if total_rows != 1 else ''} "
            f"across {len(columns)} columns."
            + (
                ""
                if saved_search_id
                else " (Aggregate result — no search saved. Include "
                "message_control_id and/or accession_number in the "
                "SELECT to make the result chainable / exportable.)"
            )
        )
    parts.append("")
    parts.append(f"Columns: {', '.join(columns)}")

    if negation_applied and wrapped_patterns:
        pairs = ", ".join(f"{orig!r}→{norm!r}" for orig, norm in wrapped_patterns)
        parts.append(
            f"Note: short bare terms auto-wrapped in word boundaries "
            f"({pairs}); pass a regex with metacharacters to opt out."
        )

    iframe_will_render = bool(sample_rows and saved_search_id)
    if iframe_will_render:
        parts.append("")
        parts.append(
            "USER DISPLAY: an interactive table of these rows is rendered "
            "below your reply (sortable columns, header filters, "
            "click-row-to-expand for full report + highlighted snippets). "
            "DO NOT restate the table or re-list rows in markdown — the "
            "user already sees them. Spend your reply on what the table "
            "can't carry: pattern observations, refinement suggestions, "
            "follow-up queries worth running, a one-sentence summary. "
            "The sample table below this directive is FOR YOUR REASONING "
            "ONLY; do not echo it back."
        )

    if sample_rows:
        parts.append("")
        breakdown: dict[str, int] = {}
        for r in sample_rows:
            lbl = r.get("evidence")
            if lbl:
                breakdown[lbl] = breakdown.get(lbl, 0) + 1
        annotation = (
            f" ({', '.join(f'{n} {lbl}' for lbl, n in breakdown.items())})"
            if breakdown
            else ""
        )
        parts.append(
            f"Sample for your reasoning ({len(sample_rows)} of "
            f"{total_rows} rows, user sees these + the rest in the table "
            f"below){annotation}:"
        )
        hidden = {"message_control_id"}
        visible_cols = [
            c for c in columns if c not in TEXT_COLUMNS_TO_STRIP and c not in hidden
        ]
        for extra in ("evidence", "snippets", "reason"):
            if any(extra in r for r in sample_rows) and extra not in visible_cols:
                visible_cols.append(extra)

        def _md_cell(value: Any) -> str:
            s = str(value if value is not None else "")
            s = s.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n")
            s = s.replace("|", "\\|")
            if len(s) > 140:
                cut = s[:140]
                sp = cut.rfind(" ")
                if sp > 100:
                    cut = cut[:sp]
                s = cut + "…"
            return s

        header = "| " + " | ".join(visible_cols) + " |"
        sep = "|" + "|".join("---" for _ in visible_cols) + "|"
        parts.append(header)
        parts.append(sep)
        for row in sample_rows:
            parts.append(
                "| " + " | ".join(_md_cell(row.get(c, "")) for c in visible_cols) + " |"
            )
        if total_rows > len(sample_rows):
            parts.append(
                f"\n... and {total_rows - len(sample_rows)} more rows are "
                f"NOT in this sample — they're in the saved search and "
                f"in the user's interactive table. Reason about the search "
                f"as a whole using the count; the sample is only a peek."
            )

    if saved_search_id:
        parts.append("")
        parts.append(
            f"Internal search handle for chaining: {saved_search_id}. "
            f"Keep this backstage; only mention it to the user when you "
            f"need to chain a follow-up query. To spot-check flagged "
            f"rows in a follow-up read_reports call, pass labels of "
            f"flagged. XNAT export is a button — only mention it when "
            f"the user explicitly says they are ready to export."
        )
    return "\n".join(parts)
