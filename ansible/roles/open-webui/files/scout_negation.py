"""
title: Scout Negation
description: Shared negation regex + helpers used by Scout's search_reports
             tool and the Send-to-XNAT Action. Patterns are ported from the
             cohort_builder.py playbook (analytics/notebooks/cohort/) and must
             stay in sync with it. Single source of truth — do not duplicate
             these lists into other files.
author: Scout Team
version: 0.1.0
"""

from __future__ import annotations

import html as html_module
import re
from typing import Optional

# 12-regex list ported from analytics/notebooks/cohort/cohort_builder.py
# (lines 327-420). 50-char context window for negation lookback.
DEFAULT_NEGATION_PATTERNS = [
    r"no\s+(mri?\s+)?evidence",
    r"without\s+(mri?\s+)?evidence",
    r"negative\s+for",
    r"absence\s+of",
    r"ruled?\s+out",
    r"rules?\s+out",
    r"excluding",
    r"excluded",
    r"evaluat(?:e|ion)\s+(for)?",
    r"concern\s+(for)?",
    r"no\s+\w+\s+(suggest|indication|sign)",
    r"no\s+",
]

NEGATION_CONTEXT_CHARS = 50


def default_negation_regex() -> re.Pattern:
    return re.compile("|".join(DEFAULT_NEGATION_PATTERNS), re.IGNORECASE)


def check_negation_before_match(
    report_lower: str,
    match_start: int,
    negation_regex: re.Pattern,
    context_chars: int = NEGATION_CONTEXT_CHARS,
) -> Optional[re.Match]:
    """Return the negation Match found in the N chars before match_start, or None."""
    start_pos = max(0, match_start - context_chars)
    context_before = report_lower[start_pos:match_start]
    return negation_regex.search(context_before)


def has_positive_mention(
    report_text: Optional[str],
    search_patterns: list[str],
    negation_regex: Optional[re.Pattern] = None,
) -> bool:
    """True if any search pattern matches without preceding negation."""
    if not report_text or not search_patterns:
        return False
    if negation_regex is None:
        negation_regex = default_negation_regex()

    report_lower = report_text.lower()
    for pattern in search_patterns:
        try:
            for match in re.finditer(pattern, report_lower, re.IGNORECASE | re.DOTALL):
                if not check_negation_before_match(
                    report_lower, match.start(), negation_regex
                ):
                    return True
        except re.error:
            continue
    return False


def find_negation_excerpt(
    report_text: Optional[str],
    search_patterns: list[str],
    negation_regex: Optional[re.Pattern] = None,
    context_chars: int = NEGATION_CONTEXT_CHARS,
) -> str:
    """Return a short '...negated phrase + matched term + tail...' excerpt for the
    first match where a negation precedes the search pattern. Empty string if no
    negation/match pair is found. Used to populate `why_excluded` on rows."""
    if not report_text or not search_patterns:
        return ""
    if negation_regex is None:
        negation_regex = default_negation_regex()

    report_lower = report_text.lower()
    for pattern in search_patterns:
        try:
            for match in re.finditer(pattern, report_lower, re.IGNORECASE | re.DOTALL):
                neg = check_negation_before_match(
                    report_lower, match.start(), negation_regex
                )
                if neg:
                    excerpt_start = max(0, match.start() - context_chars)
                    excerpt_end = min(len(report_text), match.end() + context_chars)
                    snippet = report_text[excerpt_start:excerpt_end].strip()
                    return f"...{snippet}..." if snippet else ""
        except re.error:
            continue
    return ""


def apply_negation(
    rows: list[dict],
    search_patterns: list[str],
    text_column: str = "report_text",
) -> tuple[bool, int, int]:
    """Annotate each row in-place with `included` and `why_excluded`.
    Returns (was_applied, included_count, excluded_count).

    `included` is the design-aligned column name (replaces `included_in_cohort`).
    `why_excluded` carries a context excerpt for spot-checking — empty for
    included rows."""
    if not search_patterns:
        for row in rows:
            row["included"] = True
            row["why_excluded"] = ""
        return False, len(rows), 0

    negation_regex = default_negation_regex()
    included = 0
    for row in rows:
        text = row.get(text_column)
        positive = has_positive_mention(text, search_patterns, negation_regex)
        row["included"] = positive
        row["why_excluded"] = (
            ""
            if positive
            else find_negation_excerpt(text, search_patterns, negation_regex)
        )
        if positive:
            included += 1

    return True, included, len(rows) - included


def highlight_text(
    text: str,
    search_patterns: list[str],
    negation_regex: Optional[re.Pattern] = None,
    context_chars: int = NEGATION_CONTEXT_CHARS,
) -> str:
    """HTML-escape `text`, then wrap positive search matches in a green span and
    matches preceded by negation in a red span. Used by the XNAT Action's report
    cards (and by `read_reports` when rendering with negation context)."""
    if not text or not search_patterns:
        return html_module.escape(text or "")
    if negation_regex is None:
        negation_regex = default_negation_regex()

    lowered = text.lower()
    spans: list[tuple[int, int, bool]] = []  # (start, end, is_negated)
    for pattern in search_patterns:
        try:
            for match in re.finditer(pattern, lowered, re.IGNORECASE | re.DOTALL):
                negated = bool(
                    check_negation_before_match(
                        lowered, match.start(), negation_regex, context_chars
                    )
                )
                spans.append((match.start(), match.end(), negated))
        except re.error:
            continue

    if not spans:
        return html_module.escape(text)

    spans.sort()
    merged: list[tuple[int, int, bool]] = []
    for start, end, negated in spans:
        if merged and start <= merged[-1][1]:
            prev_start, prev_end, prev_neg = merged[-1]
            merged[-1] = (prev_start, max(prev_end, end), prev_neg or negated)
        else:
            merged.append((start, end, negated))

    out: list[str] = []
    cursor = 0
    for start, end, negated in merged:
        out.append(html_module.escape(text[cursor:start]))
        cls = "neg-match" if negated else "pos-match"
        out.append(f'<span class="{cls}">{html_module.escape(text[start:end])}</span>')
        cursor = end
    out.append(html_module.escape(text[cursor:]))
    return "".join(out)
