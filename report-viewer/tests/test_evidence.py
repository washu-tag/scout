"""Tests for deriving match evidence from a saved cohort query."""

from __future__ import annotations

import pytest
import sqlglot
from sqlglot import exp

from scout_report_viewer.evidence import (
    EV_COLUMNS,
    build_plan,
    highlight_hits_expression,
    with_evidence,
)

# The shape the chat system prompt templates: a diagnosis axis ORed with a text
# axis, each text source carrying its own veto, plus a report_text fallback.
CANONICAL = """SELECT primary_report_identifier, accession_number, modality, service_name
FROM reports_latest
WHERE REGEXP_LIKE(service_name, '(?i)(brain|head)')
  AND (
    any_match(diagnoses, d -> d.diagnosis_code LIKE 'C71%')
    OR (
      (REGEXP_LIKE(COALESCE(report_section_impression, ''), '(?is)(?:glioblastoma|gbm)')
       AND NOT REGEXP_LIKE(COALESCE(report_section_impression, ''), '(?is)(?:no|without)[^.;:]*(?:glioblastoma|gbm)'))
      OR (REGEXP_LIKE(COALESCE(report_section_findings, ''), '(?is)(?:glioblastoma|gbm)')
       AND NOT REGEXP_LIKE(COALESCE(report_section_findings, ''), '(?is)(?:no|without)[^.;:]*(?:glioblastoma|gbm)'))
      OR (COALESCE(TRIM(report_section_impression), '') = ''
          AND COALESCE(TRIM(report_section_findings), '') = ''
          AND REGEXP_LIKE(report_text, '(?is)(?:glioblastoma|gbm)')
          AND NOT REGEXP_LIKE(report_text, '(?is)(?:no|without)[^.;:]*(?:glioblastoma|gbm)'))
    )
  )
LIMIT 50000"""

TEXT_ONLY = (
    "SELECT primary_report_identifier, accession_number FROM reports_latest "
    "WHERE REGEXP_LIKE(COALESCE(report_section_impression, ''), '(?is)stroke') "
    "AND NOT REGEXP_LIKE(COALESCE(report_section_impression, ''), '(?is)no[^.;:]*stroke')"
)


def select_aliases(sql: str) -> list[str]:
    return [
        e.alias_or_name for e in sqlglot.parse_one(sql, dialect="trino").expressions
    ]


def test_classifies_each_source_and_pairs_its_veto() -> None:
    plan = build_plan(CANONICAL)
    assert plan is not None
    assert plan.has_dx_axis is True
    # Ordered so the span comes from the most specific section that matched.
    assert [p.column for p in plan.positives] == [
        "report_section_impression",
        "report_section_findings",
        "report_text",
    ]
    for pos in plan.positives:
        veto = plan.veto_for[pos]
        assert veto.negated and veto.column == pos.column
        assert veto.pattern.endswith("(?:glioblastoma|gbm)")


def test_a_veto_is_never_read_as_a_positive() -> None:
    plan = build_plan(CANONICAL)
    assert plan is not None
    assert all(not p.negated for p in plan.positives)
    assert all("[^.;:]*" not in p.pattern for p in plan.positives)


def test_where_clause_is_untouched() -> None:
    """The filter is literally the same characters, so it cannot select different rows."""
    out, has_evidence = with_evidence(CANONICAL)
    assert has_evidence is True
    assert out.endswith(CANONICAL[CANONICAL.index("FROM reports_latest") :])


def test_adds_exactly_the_evidence_columns() -> None:
    before = select_aliases(CANONICAL)
    out, _ = with_evidence(CANONICAL)
    after = select_aliases(out)
    assert after[: len(before)] == before
    assert after[len(before) :] == list(EV_COLUMNS)


def test_include_span_reads_the_column_its_pattern_matched() -> None:
    out, _ = with_evidence(CANONICAL)
    for column in (
        "report_section_impression",
        "report_section_findings",
        "report_text",
    ):
        assert f"REGEXP_EXTRACT({column}," in out


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT primary_report_identifier FROM reports_latest LIMIT 50000",
        "SELECT primary_report_identifier FROM reports_latest WHERE modality = 'MR'",
        # A scope regex describes no part of the report body.
        "SELECT primary_report_identifier FROM reports_latest "
        "WHERE REGEXP_LIKE(service_name, '(?i)chest')",
        # Diagnosis-only cohorts are exact; there is nothing to explain.
        "SELECT primary_report_identifier FROM reports_latest "
        "WHERE any_match(diagnoses, d -> d.diagnosis_code LIKE 'C71%')",
        # Extra columns would change what counts as a duplicate.
        "SELECT DISTINCT primary_report_identifier FROM reports_latest "
        "WHERE REGEXP_LIKE(report_text, '(?is)stroke')",
        "SELECT modality, COUNT(*) FROM reports_latest "
        "WHERE REGEXP_LIKE(report_text, '(?is)stroke') GROUP BY modality",
        # An exclusion cohort has no positive to point at.
        "SELECT primary_report_identifier FROM reports_latest "
        "WHERE NOT REGEXP_LIKE(report_text, '(?is)stroke')",
        "SELECT FROM WHERE regexp_like(",
        "",
    ],
)
def test_unsupported_shapes_return_the_original_sql(sql: str) -> None:
    out, has_evidence = with_evidence(sql)
    assert has_evidence is False
    assert out == sql


def test_rewriting_twice_does_not_stack_columns() -> None:
    once, _ = with_evidence(CANONICAL)
    twice, _ = with_evidence(once)
    assert select_aliases(twice).count("ev_source") == 1


def test_exclude_span_is_set_even_when_another_source_admitted_the_row() -> None:
    """One section can admit the row while another carries the exclusion."""
    out, _ = with_evidence(CANONICAL)
    exclude = next(
        e
        for e in sqlglot.parse_one(out, dialect="trino").expressions
        if e.alias_or_name == "ev_negative_span"
    )
    # Keyed only on the exclusion patterns, never on which source admitted.
    assert "ev_source" not in exclude.sql(dialect="trino")


def test_source_reports_one_text_axis() -> None:
    """Section parsing is heuristic, so which section matched is not published."""
    out, _ = with_evidence(CANONICAL)
    assert "'text'" in out
    for section in ("'impression'", "'findings'", "'report_text'"):
        assert section not in out


def test_the_diagnosis_arm_is_evaluated_not_assumed() -> None:
    """An unexplained row reads unknown rather than blaming a code that may
    not have matched."""
    out, _ = with_evidence(CANONICAL)
    assert "CARDINALITY(FILTER(diagnoses" in out
    assert "ELSE 'diagnosis_code'" not in out


def test_each_positive_pairs_with_the_veto_in_its_own_and() -> None:
    """Two concepts on one column must not share a veto."""
    sql = (
        "SELECT primary_report_identifier FROM reports_latest WHERE "
        "(REGEXP_LIKE(COALESCE(report_section_impression, ''), '(?is)nodule') "
        "AND NOT REGEXP_LIKE(COALESCE(report_section_impression, ''), '(?is)no[^.;:]*nodule')) "
        "AND (REGEXP_LIKE(COALESCE(report_section_impression, ''), '(?is)emphysema') "
        "AND NOT REGEXP_LIKE(COALESCE(report_section_impression, ''), '(?is)no[^.;:]*emphysema'))"
    )
    plan = build_plan(sql)
    assert plan is not None
    pairing = {pos.pattern: veto.pattern for pos, veto in plan.veto_for.items()}
    assert pairing == {
        "(?is)nodule": "(?is)no[^.;:]*nodule",
        "(?is)emphysema": "(?is)no[^.;:]*emphysema",
    }


def test_the_report_text_arm_keeps_its_blank_section_guard() -> None:
    """report_text is a fallback. Without the guard a HISTORY mention would be
    reported as the reason for a row the query admitted on its diagnosis code."""
    out, _ = with_evidence(CANONICAL)
    arm = next(
        line
        for line in out.splitlines()
        if "REGEXP_EXTRACT(report_text," in line and "THEN" in line
    )
    assert "COALESCE(TRIM(report_section_impression), '') = ''" in arm
    assert "COALESCE(TRIM(report_section_findings), '') = ''" in arm


def test_without_a_diagnosis_axis_nothing_claims_a_code() -> None:
    out, _ = with_evidence(TEXT_ONLY)
    assert "'diagnosis_code'" not in out


def test_single_quotes_in_a_pattern_are_escaped() -> None:
    sql = (
        "SELECT primary_report_identifier FROM reports_latest "
        "WHERE REGEXP_LIKE(report_text, '(?is)patient''s stroke')"
    )
    out, has_evidence = with_evidence(sql)
    assert has_evidence is True
    span = next(
        e
        for e in sqlglot.parse_one(out, dialect="trino").expressions
        if e.alias_or_name == "ev_positive_span"
    )
    assert "(?is)patient's stroke" in [
        n.this for n in span.find_all(exp.Literal) if n.is_string
    ]


def test_from_inside_a_string_literal_is_not_the_splice_point() -> None:
    sql = (
        "SELECT primary_report_identifier FROM reports_latest "
        "WHERE REGEXP_LIKE(report_text, '(?is)away from midline')"
    )
    out, has_evidence = with_evidence(sql)
    assert has_evidence is True
    assert out.endswith(sql[sql.index("FROM reports_latest") :])


def test_highlight_expression_is_valid_trino() -> None:
    expression = highlight_hits_expression(CANONICAL)
    assert expression is not None
    sqlglot.parse_one(
        f"SELECT id, {expression} AS hits FROM reports_curated", dialect="trino"
    )


def test_highlight_expression_covers_both_polarities_per_column() -> None:
    expression = highlight_hits_expression(CANONICAL)
    assert expression is not None
    assert expression.count("'positive'") == 3
    assert expression.count("'negative'") == 3
    assert "REGEXP_POSITION" in expression


def test_highlight_expression_guards_the_empty_case() -> None:
    """sequence(1, 0) counts down in Trino, so zero matches needs its own arm."""
    expression = highlight_hits_expression(CANONICAL)
    assert expression is not None
    assert expression.count("CARDINALITY(") >= 6
    assert "CAST(ARRAY[] AS ARRAY(" in expression


def test_no_text_predicate_means_no_highlight_expression() -> None:
    sql = "SELECT primary_report_identifier FROM reports_latest WHERE modality = 'MR'"
    assert highlight_hits_expression(sql) is None


def test_a_nested_predicate_is_left_alone() -> None:
    """A column in a subquery is in scope there, not in the SELECT we splice."""
    sql = (
        "SELECT primary_report_identifier FROM reports_dx "
        "WHERE primary_report_identifier IN ("
        "SELECT primary_report_identifier FROM reports_latest "
        "WHERE REGEXP_LIKE(report_section_impression, '(?is)stroke'))"
    )
    out, has_evidence = with_evidence(sql)
    assert has_evidence is False
    assert out == sql


def test_highlights_cover_the_column_the_viewer_renders() -> None:
    """The row panel shows report_text, so a section-only offset marks nothing."""
    expression = highlight_hits_expression(CANONICAL)
    assert expression is not None
    assert expression.count("ROW('report_text'") >= 2
