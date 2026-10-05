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
        (veto,) = plan.veto_for[pos]
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


def test_dx_text_travels_with_the_code() -> None:
    out, _ = with_evidence(CANONICAL)
    assert "x.diagnosis_code_text), ', ') AS ev_dx_text" in out


def test_adds_exactly_the_evidence_columns() -> None:
    before = select_aliases(CANONICAL)
    out, _ = with_evidence(CANONICAL)
    after = select_aliases(out)
    assert after[: len(before)] == before
    assert after[len(before) :] == list(EV_COLUMNS)


def test_include_span_reads_the_subject_its_pattern_matched() -> None:
    """Each arm as the query wrote it: the sections are COALESCEd, report_text
    is not."""
    out, _ = with_evidence(CANONICAL)
    for subject in (
        "COALESCE(report_section_impression, '')",
        "COALESCE(report_section_findings, '')",
        "report_text",
    ):
        assert f"REGEXP_EXTRACT({subject}," in out


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT primary_report_identifier FROM reports_latest LIMIT 50000",
        "SELECT primary_report_identifier FROM reports_latest WHERE modality = 'MR'",
        # A scope regex describes no part of the report body.
        "SELECT primary_report_identifier FROM reports_latest "
        "WHERE REGEXP_LIKE(service_name, '(?i)chest')",
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
    """A row no arm explains reads NULL rather than blaming a code that may
    not have matched."""
    out, _ = with_evidence(CANONICAL)
    assert "ANY_MATCH(diagnoses" in out
    assert "ELSE 'diagnosis_code'" not in out


def test_every_veto_in_a_conjunction_guards_every_positive_in_it() -> None:
    """All of them must be false for the arm to admit, so if another OR branch
    let the row in, this arm should not claim it."""
    sql = (
        "SELECT primary_report_identifier FROM reports_latest WHERE "
        "(REGEXP_LIKE(COALESCE(report_section_impression, ''), '(?is)nodule') "
        "AND NOT REGEXP_LIKE(COALESCE(report_section_impression, ''), '(?is)no[^.;:]*nodule')) "
        "AND (REGEXP_LIKE(COALESCE(report_section_impression, ''), '(?is)emphysema') "
        "AND NOT REGEXP_LIKE(COALESCE(report_section_impression, ''), '(?is)no[^.;:]*emphysema'))"
    )
    plan = build_plan(sql)
    assert plan is not None
    pairing = {
        pos.pattern: [v.pattern for v in vetoes]
        for pos, vetoes in plan.veto_for.items()
    }
    both = ["(?is)no[^.;:]*emphysema", "(?is)no[^.;:]*nodule"]
    assert {k: sorted(v) for k, v in pairing.items()} == {
        "(?is)nodule": both,
        "(?is)emphysema": both,
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


def test_occurrence_index_is_cast_to_integer() -> None:
    """SEQUENCE yields BIGINT but regexp_position's occurrence arg is INTEGER.
    Without the cast Trino rejects the whole expression and marks vanish."""
    expression = highlight_hits_expression(CANONICAL)
    assert expression is not None
    assert "CAST(i AS INTEGER)" in expression
    assert ", 1, i)" not in expression


def test_source_names_text_and_code_when_both_matched() -> None:
    out, _ = with_evidence(CANONICAL)
    source = next(
        e
        for e in sqlglot.parse_one(out, dialect="trino").expressions
        if e.alias_or_name == "ev_source"
    )
    rendered = source.sql(dialect="trino")
    for value in ("'text_and_code'", "'text'", "'diagnosis_code'"):
        assert value in rendered
    # text_and_code must be tested first, or the text arm swallows it.
    assert rendered.index("'text_and_code'") < rendered.index("'text'")


def test_diagnosis_axis_is_reused_not_rebuilt_from_its_like_patterns() -> None:
    """A row admitted on diagnosis_code_text read as unexplained, because the
    axis was rebuilt as `diagnosis_code LIKE` and dropped every other test."""
    sql = (
        "SELECT primary_report_identifier FROM reports_latest WHERE "
        "any_match(diagnoses, d -> d.diagnosis_code LIKE 'I26%' "
        "OR LOWER(d.diagnosis_code_text) LIKE '%pulmonary embolism%') "
        "OR REGEXP_LIKE(COALESCE(report_section_impression, ''), '(?is)embolism')"
    )
    out, has_evidence = with_evidence(sql)
    assert has_evidence
    assert "diagnosis_code_text" in out
    # The whole axis decides the arm, so I27.82 titled "pulmonary embolism"
    # lands in diagnosis_code rather than falling through to NULL.
    source = next(
        e
        for e in sqlglot.parse_one(out, dialect="trino").expressions
        if e.alias_or_name == "ev_source"
    ).sql(dialect="trino")
    assert "LOWER(d.diagnosis_code_text) LIKE '%pulmonary embolism%'" in source


DX_ONLY = (
    "SELECT primary_report_identifier, accession_number FROM reports_latest WHERE "
    "any_match(diagnoses, d -> d.diagnosis_code LIKE 'J1%') "
    "AND REGEXP_LIKE(service_name, '(?i)chest')"
)


def test_a_diagnosis_only_cohort_still_gets_evidence() -> None:
    """A scope-only regexp is not a text axis, and the query is still explainable
    by the codes it matched."""
    out, has_evidence = with_evidence(DX_ONLY)
    assert has_evidence
    assert select_aliases(out)[-len(EV_COLUMNS) :] == list(EV_COLUMNS)
    assert "x.diagnosis_code), ', ') AS ev_dx_codes" in out


def test_a_diagnosis_only_cohort_claims_no_text() -> None:
    out, _ = with_evidence(DX_ONLY)
    source = next(
        e
        for e in sqlglot.parse_one(out, dialect="trino").expressions
        if e.alias_or_name == "ev_source"
    ).sql(dialect="trino")
    assert "'diagnosis_code'" in source
    for value in ("'text'", "'text_and_code'"):
        assert value not in source
    assert "CAST(NULL AS VARCHAR) AS ev_positive_span" in with_evidence(DX_ONLY)[0]


def test_no_text_leaves_means_no_highlight_expression() -> None:
    """An empty concatenation would be invalid SQL, not an empty result."""
    assert highlight_hits_expression(DX_ONLY) is None


def test_no_blank_section_guard_when_the_query_did_not_ask_for_one() -> None:
    """Synthesising it rejects rows the WHERE admitted, which read as unexplained."""
    sql = (
        "SELECT primary_report_identifier FROM reports_latest WHERE "
        "REGEXP_LIKE(report_section_impression, '(?is)x') "
        "OR REGEXP_LIKE(report_text, '(?is)y')"
    )
    out, _ = with_evidence(sql)
    assert "TRIM(" not in out


def test_a_veto_does_not_cross_an_or_branch() -> None:
    """Borrowing the other arm's veto reports rows the query admitted as ruled out."""
    sql = (
        "SELECT primary_report_identifier FROM reports_latest WHERE "
        "((REGEXP_LIKE(report_section_impression, '(?is)a') "
        "AND NOT REGEXP_LIKE(report_section_impression, '(?is)no a')) "
        "OR REGEXP_LIKE(report_section_impression, '(?is)b')) AND year = 2026"
    )
    plan = build_plan(sql)
    assert plan is not None
    pairing = {
        pos.pattern: [v.pattern for v in vetoes]
        for pos, vetoes in plan.veto_for.items()
    }
    assert pairing == {"(?is)a": ["(?is)no a"]}


def test_a_wrapped_subject_is_tested_as_written() -> None:
    """Dropping the wrapper tested the raw column case-sensitively, so a row
    the query admitted on "Pneumonia" got no span."""
    sql = (
        "SELECT primary_report_identifier FROM reports_latest "
        "WHERE REGEXP_LIKE(LOWER(report_text), 'pneumonia')"
    )
    out, _ = with_evidence(sql)
    assert "REGEXP_LIKE(LOWER(report_text), '(?i)pneumonia')" in out
    assert "REGEXP_EXTRACT(LOWER(report_text), '(?i)pneumonia')" in out
    # The highlight reads the raw column, so it needs the folded pattern.
    expression = highlight_hits_expression(sql)
    assert expression is not None and "'(?i)pneumonia'" in expression


def test_an_inline_flag_is_not_doubled() -> None:
    """Only skipped when the pattern already sets i, so a flag part way in
    still gets the fold."""
    lead, _ = with_evidence(
        "SELECT primary_report_identifier FROM reports_latest "
        "WHERE REGEXP_LIKE(LOWER(report_text), '(?is)pneumonia')"
    )
    assert "(?i)(?is)" not in lead
    mid, _ = with_evidence(
        "SELECT primary_report_identifier FROM reports_latest "
        "WHERE REGEXP_LIKE(LOWER(report_text), 'abc(?i)def')"
    )
    assert "'(?i)abc(?i)def'" in mid


def test_the_report_text_veto_keeps_its_blank_section_guard() -> None:
    """The query only rules out on report_text when no section parsed, so a
    HISTORY line must not flag a row admitted on its impression."""
    out, _ = with_evidence(CANONICAL)
    arm = next(
        line
        for line in out.splitlines()
        if "WHEN (REGEXP_LIKE(report_text," in line and "no|without" in line
    )
    assert "COALESCE(TRIM(report_section_impression), '') = ''" in arm
    assert "COALESCE(TRIM(report_section_findings), '') = ''" in arm


def test_highlights_are_not_blank_section_guarded() -> None:
    """The panel renders report_text, so guarding these would drop every mark
    on a report that has sections."""
    expression = highlight_hits_expression(CANONICAL)
    assert expression is not None
    assert "TRIM(" not in expression


def test_a_subquery_diagnosis_test_stays_in_its_subquery() -> None:
    """Copying an alias the outer SELECT cannot see makes Trino reject the
    rewrite, so the cohort scans twice."""
    sql = (
        "SELECT r.primary_report_identifier FROM reports_latest r WHERE "
        "REGEXP_LIKE(r.report_text, '(?is)stroke') AND EXISTS ("
        "SELECT 1 FROM reports_latest r2 WHERE r2.epic_mrn = r.epic_mrn "
        "AND any_match(r2.diagnoses, d -> d.diagnosis_code LIKE 'I63%'))"
    )
    plan = build_plan(sql)
    assert plan is not None
    assert plan.dx_tests == []
    assert "r2." not in with_evidence(sql)[0].split("FROM reports_latest")[0]


def test_every_diagnosis_axis_contributes_its_codes() -> None:
    """Taking only the first left a row matching the second with no codes."""
    sql = (
        "SELECT primary_report_identifier FROM reports_latest WHERE "
        "any_match(diagnoses, d -> d.diagnosis_code LIKE 'J1%') "
        "OR any_match(diagnoses, d -> d.diagnosis_code LIKE 'J2%')"
    )
    out, _ = with_evidence(sql)
    assert "'J1%') || FILTER(diagnoses, d -> d.diagnosis_code LIKE 'J2%')" in out


@pytest.mark.parametrize(
    ("spelling", "negated"),
    [
        ("REGEXP_LIKE(report_text, '(?is)no a') = false", True),
        ("REGEXP_LIKE(report_text, '(?is)no a') <> true", True),
        ("REGEXP_LIKE(report_text, '(?is)no a') IS NOT TRUE", True),
        ("REGEXP_LIKE(report_text, '(?is)no a') = true", False),
    ],
)
def test_negation_without_a_not(spelling: str, negated: bool) -> None:
    """Read as a positive, the ruled-out phrase became the row's evidence."""
    plan = build_plan(
        "SELECT primary_report_identifier FROM reports_latest WHERE "
        f"REGEXP_LIKE(report_text, '(?is)a') AND {spelling}"
    )
    assert plan is not None
    assert [v.pattern for v in plan.vetoes] == (["(?is)no a"] if negated else [])


def test_every_veto_under_one_not_guards_the_positive() -> None:
    """NOT (v1 OR v2) rules out both, so an arm testing only v1 can report the
    positive phrase for a row that branch would have rejected."""
    sql = (
        "SELECT primary_report_identifier FROM reports_latest WHERE "
        "(REGEXP_LIKE(report_section_impression, '(?is)a') "
        " AND NOT (REGEXP_LIKE(report_section_impression, '(?is)no a') "
        "       OR REGEXP_LIKE(report_section_impression, '(?is)ruled out a'))) "
        "OR REGEXP_LIKE(report_section_findings, '(?is)b')"
    )
    plan = build_plan(sql)
    assert plan is not None
    positive = next(p for p in plan.positives if p.pattern == "(?is)a")
    assert sorted(v.pattern for v in plan.veto_for[positive]) == [
        "(?is)no a",
        "(?is)ruled out a",
    ]
    arm = next(
        line for line in with_evidence(sql)[0].splitlines() if "THEN 'text'" in line
    )
    assert arm.count("NOT REGEXP_LIKE") == 2


def test_two_blank_section_arms_are_ored() -> None:
    """Keeping only the first made rows admitted by the second unexplained."""
    out, _ = with_evidence(
        "SELECT primary_report_identifier FROM reports_latest WHERE "
        "(COALESCE(TRIM(report_section_impression), '') = '' "
        " AND REGEXP_LIKE(report_text, '(?is)s')) "
        "OR (COALESCE(TRIM(report_section_findings), '') = '' "
        " AND REGEXP_LIKE(report_text, '(?is)s'))"
    )
    arm = next(line for line in out.splitlines() if "THEN 'text'" in line)
    assert "OR COALESCE(TRIM(report_section_findings), '') = ''" in arm


def test_an_unguarded_arm_drops_the_guard_entirely() -> None:
    """That occurrence admits on its own, so requiring another arm's guard
    leaves rows it matched reading as unexplained."""
    out, _ = with_evidence(
        "SELECT primary_report_identifier FROM reports_latest WHERE "
        "REGEXP_LIKE(report_section_impression, '(?is)x') "
        "OR (COALESCE(TRIM(report_section_impression), '') = '' "
        " AND REGEXP_LIKE(report_text, '(?is)a')) "
        "OR (REGEXP_LIKE(report_text, '(?is)a') AND modality = 'CT')"
    )
    arm = next(line for line in out.splitlines() if "THEN 'text'" in line)
    assert "TRIM(" not in arm


def test_an_unhandled_shape_costs_the_evidence_not_the_cohort(monkeypatch) -> None:
    """The plan reads SQL a model wrote, so it must never raise at the caller."""
    import scout_report_viewer.evidence as evidence

    def boom(_sql: str):
        raise RuntimeError("unhandled sql shape")

    monkeypatch.setattr(evidence, "build_plan", boom)
    assert evidence.with_evidence(CANONICAL) == (CANONICAL, False)
    assert evidence.highlight_hits_expression(CANONICAL) is None


def test_a_chain_of_vetoes_all_guard_the_positive() -> None:
    """A AND NOT V1 AND NOT V2 nests, so the nearest AND holds only one."""
    out, _ = with_evidence(
        "SELECT primary_report_identifier FROM reports_latest WHERE "
        "REGEXP_LIKE(report_text, '(?is)a') "
        "AND NOT REGEXP_LIKE(report_text, '(?is)v1') "
        "AND NOT REGEXP_LIKE(report_text, '(?is)v2')"
    )
    arm = next(line for line in out.splitlines() if "THEN 'text'" in line)
    assert arm.count("NOT REGEXP_LIKE") == 2


def test_an_is_null_section_guard_is_kept_as_written() -> None:
    """IS NULL and TRIM(col) = '' disagree on a whitespace-only section."""
    out, _ = with_evidence(
        "SELECT primary_report_identifier FROM reports_latest WHERE "
        "REGEXP_LIKE(report_section_impression, '(?is)nodule') "
        "OR (report_section_impression IS NULL AND report_section_findings IS NULL "
        "AND REGEXP_LIKE(report_text, '(?is)nodule') "
        "AND NOT REGEXP_LIKE(report_text, '(?is)no nodule'))"
    )
    arm = next(line for line in out.splitlines() if "THEN 'text'" in line)
    assert "report_section_impression IS NULL" in arm
    assert "TRIM(" not in arm
    # The veto is gated the same way, or a HISTORY line flags a sectioned row.
    veto_arm = next(
        line
        for line in out.splitlines()
        if "REGEXP_EXTRACT(report_text" in line and "no nodule" in line
    )
    assert "report_section_impression IS NULL" in veto_arm


@pytest.mark.parametrize(
    ("where", "expected"),
    [
        # Brackets are grouping, so a positive inside them still reaches the
        # veto ANDed with the group.
        (
            "((REGEXP_LIKE(report_section_impression, '(?is)A') AND modality = 'CT') "
            "AND NOT REGEXP_LIKE(report_section_impression, '(?is)V1')) "
            "OR any_match(diagnoses, d -> d.diagnosis_code LIKE 'I2%')",
            ["(?is)V1"],
        ),
        (
            "((REGEXP_LIKE(report_section_impression, '(?is)A') "
            "AND NOT REGEXP_LIKE(report_section_impression, '(?is)V1')) "
            "AND NOT REGEXP_LIKE(report_section_impression, '(?is)V2')) "
            "OR any_match(diagnoses, d -> d.diagnosis_code LIKE 'I2%')",
            ["(?is)V1", "(?is)V2"],
        ),
        # A veto on another column still has to be false for the arm to admit.
        (
            "(REGEXP_LIKE(report_section_impression, '(?is)A') "
            "AND NOT REGEXP_LIKE(report_text, '(?is)V1')) "
            "OR any_match(diagnoses, d -> d.diagnosis_code LIKE 'I2%')",
            ["(?is)V1"],
        ),
    ],
)
def test_vetoes_reach_a_bracketed_positive(where: str, expected: list[str]) -> None:
    plan = build_plan(
        f"SELECT primary_report_identifier FROM reports_latest WHERE {where}"
    )
    assert plan is not None
    positive = next(p for p in plan.positives if p.pattern == "(?is)A")
    assert sorted(v.pattern for v in plan.veto_for[positive]) == expected


def test_planning_scans_each_sibling_branch_once(monkeypatch) -> None:
    """Rescanning the whole conjunction at every level made this quadratic.
    Counted, not timed, so a loaded runner cannot fail it."""
    import scout_report_viewer.evidence as ev

    scanned: list[int] = []
    real = ev._conjoined_ids

    def counting(node):
        result = real(node)
        scanned.append(len(result))
        return result

    monkeypatch.setattr(ev, "_conjoined_ids", counting)
    positives = " AND ".join(f"REGEXP_LIKE(report_text, '(?is)p{i}')" for i in range(4))
    vetoes = " AND ".join(
        f"NOT REGEXP_LIKE(report_text, '(?is)v{i}')" for i in range(2)
    )
    extra = " AND ".join(f"modality <> 'X{i}'" for i in range(60))
    build_plan(
        f"SELECT x FROM reports_latest WHERE {positives} AND {vetoes} AND {extra}"
    )
    # Each scan covers one predicate. Rescanning the conjunction would cover
    # every predicate in the chain, an order of magnitude more.
    assert max(scanned) < 40


def test_a_compound_not_is_not_modelled() -> None:
    """NOT (x AND v) is NOT x OR NOT v, so neither is ruled out on its own.
    Claiming v as a veto hides evidence from a row the query admitted."""
    plan = build_plan(
        "SELECT primary_report_identifier FROM reports_latest WHERE "
        "REGEXP_LIKE(report_section_impression, '(?is)tumor') "
        "AND NOT (REGEXP_LIKE(report_section_impression, '(?is)benign') "
        "AND REGEXP_LIKE(report_section_impression, '(?is)cyst'))"
    )
    assert plan is not None
    assert [p.pattern for p in plan.positives] == ["(?is)tumor"]
    assert plan.vetoes == []


def test_a_not_over_a_disjunction_is_modelled() -> None:
    """NOT (v1 OR v2) does rule out each of them."""
    plan = build_plan(
        "SELECT primary_report_identifier FROM reports_latest WHERE "
        "REGEXP_LIKE(report_text, '(?is)a') "
        "AND NOT (REGEXP_LIKE(report_text, '(?is)v1') "
        "OR REGEXP_LIKE(report_text, '(?is)v2'))"
    )
    assert plan is not None
    assert sorted(v.pattern for v in plan.vetoes) == ["(?is)v1", "(?is)v2"]


def test_a_veto_anded_onto_a_disjunction_reaches_both_arms() -> None:
    """The prompt's own (impression OR findings) pattern, with a veto added."""
    plan = build_plan(
        "SELECT primary_report_identifier FROM reports_latest WHERE "
        "((REGEXP_LIKE(report_section_impression, '(?is)A') "
        "OR REGEXP_LIKE(report_section_findings, '(?is)A')) "
        "AND NOT REGEXP_LIKE(report_section_impression, '(?is)V')) "
        "OR any_match(diagnoses, d -> d.diagnosis_code LIKE 'I2%')"
    )
    assert plan is not None
    for positive in plan.positives:
        assert [v.pattern for v in plan.veto_for[positive]] == ["(?is)V"]
