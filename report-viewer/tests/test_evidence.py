"""Tests for deriving match evidence from a saved cohort query."""

from __future__ import annotations

import pytest
import sqlglot
from sqlglot import exp

from scout_report_viewer.evidence import EV_COLUMNS, build_plan, with_evidence

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
    # Impression first: it is the radiologist's call and wins the ev_source tie.
    assert [p.column for p in plan.positives] == [
        "report_section_impression",
        "report_section_findings",
        "report_text",
    ]
    for pos in plan.positives:
        veto = plan.veto_by_column[pos.column]
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


def test_adds_exactly_the_four_evidence_columns() -> None:
    before = select_aliases(CANONICAL)
    out, _ = with_evidence(CANONICAL)
    after = select_aliases(out)
    assert after[: len(before)] == before
    assert after[len(before) :] == list(EV_COLUMNS)


def test_span_reads_the_column_its_pattern_matched() -> None:
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


def test_without_a_diagnosis_axis_nothing_claims_a_code() -> None:
    out, _ = with_evidence(TEXT_ONLY)
    assert "'diagnosis_code'" not in out
    contradicted = next(
        e
        for e in sqlglot.parse_one(out, dialect="trino").expressions
        if e.alias_or_name == "ev_contradicted"
    )
    assert (
        isinstance(contradicted.this, exp.Boolean) and contradicted.this.this is False
    )


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
        if e.alias_or_name == "ev_span"
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
