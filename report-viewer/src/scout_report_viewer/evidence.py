"""Derive match evidence from a saved cohort query.

Parses the saved SQL, finds the `REGEXP_LIKE` predicates testing a report-body
column, and splices `ev_source` / `ev_span` / `ev_negated_span` /
`ev_contradicted` onto its SELECT list.

Appended to the original SELECT rather than wrapped in a CTE: the model
projects only display columns, so the body columns are in scope here and
nowhere else. Spliced by token offset rather than regenerated from the AST, so
everything from FROM onward survives byte for byte and the rewrite cannot
change which rows come back.

Every failure path returns the original SQL.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.tokens import TokenType

log = logging.getLogger(__name__)

DIALECT = "trino"

# A REGEXP_LIKE on anything else -- `service_name`, say -- filters the cohort
# but describes no part of the report body.
TEXT_COLUMNS = frozenset(
    {
        "report_text",
        "report_section_impression",
        "report_section_findings",
        "report_section_addendum",
        "report_section_technician_note",
    }
)

# Tie-break when several sources matched: impression is the radiologist's call,
# report_text is the fallback for reports with no parsed sections.
SOURCE_ORDER = (
    "report_section_impression",
    "report_section_findings",
    "report_section_addendum",
    "report_section_technician_note",
    "report_text",
)

SOURCE_LABEL = {
    "report_section_impression": "impression",
    "report_section_findings": "findings",
    "report_section_addendum": "addendum",
    "report_section_technician_note": "technician_note",
    "report_text": "report_text",
}

EV_COLUMNS = ("ev_source", "ev_span", "ev_negated_span", "ev_contradicted")

MAX_TEXT_LEAVES = 24


@dataclass(frozen=True)
class TextLeaf:
    column: str
    pattern: str
    negated: bool


@dataclass
class EvidencePlan:
    positives: list[TextLeaf] = field(default_factory=list)
    vetoes: list[TextLeaf] = field(default_factory=list)
    veto_by_column: dict[str, TextLeaf] = field(default_factory=dict)
    has_dx_axis: bool = False


def _text_column_of(node: exp.Expression) -> str | None:
    # The subject is usually wrapped, e.g. COALESCE(report_section_impression, '').
    for col in node.find_all(exp.Column):
        if col.name in TEXT_COLUMNS:
            return col.name
    return None


def _is_negated(node: exp.Expression) -> bool:
    parent = node.parent
    while isinstance(parent, exp.Paren):
        parent = parent.parent
    return isinstance(parent, exp.Not)


def _regexp_like_parts(node: exp.Expression) -> tuple[str, str] | None:
    """(column, pattern) when `node` is a REGEXP_LIKE over a body column."""
    if isinstance(node, exp.RegexpLike):
        subject, pattern_node = node.this, node.expression
    elif isinstance(node, exp.Anonymous) and node.name.lower() == "regexp_like":
        args = node.expressions
        if len(args) < 2:
            return None
        subject, pattern_node = args[0], args[1]
    else:
        return None

    if not isinstance(pattern_node, exp.Literal) or not pattern_node.is_string:
        return None
    column = _text_column_of(subject)
    if column is None:
        return None
    return column, pattern_node.this


def _has_dx_predicate(where: exp.Expression) -> bool:
    return any(
        col.name in ("diagnoses", "diagnosis_code", "diagnosis_code_text")
        for col in where.find_all(exp.Column)
    )


def _unsupported(select: exp.Select) -> str | None:
    # DISTINCT is the one that can move the row count: extra columns change
    # what counts as a duplicate.
    if select.args.get("distinct"):
        return "SELECT DISTINCT"
    if select.args.get("group"):
        return "GROUP BY"
    if any(e.alias_or_name in EV_COLUMNS for e in select.expressions):
        return "already has evidence columns"
    return None


def build_plan(sql: str) -> EvidencePlan | None:
    """Classify the WHERE predicates worth surfacing, or None to leave the SQL alone."""
    if not sql or "regexp_like" not in sql.lower():
        return None
    try:
        tree = sqlglot.parse_one(sql, dialect=DIALECT)
    except Exception:
        log.warning(
            "evidence: could not parse saved sql; skipping rewrite", exc_info=True
        )
        return None
    if not isinstance(tree, exp.Select):
        return None

    reason = _unsupported(tree)
    if reason is not None:
        log.debug("evidence: skipping rewrite (%s)", reason)
        return None

    where = tree.args.get("where")
    if where is None:
        return None

    plan = EvidencePlan(has_dx_axis=_has_dx_predicate(where))
    seen: set[TextLeaf] = set()
    # find_all, not walk: walk's yield shape changed between sqlglot majors.
    for node in where.find_all(exp.RegexpLike, exp.Anonymous):
        parts = _regexp_like_parts(node)
        if parts is None:
            continue
        leaf = TextLeaf(column=parts[0], pattern=parts[1], negated=_is_negated(node))
        if leaf in seen:
            continue
        seen.add(leaf)
        (plan.vetoes if leaf.negated else plan.positives).append(leaf)

    if not plan.positives:
        return None
    if len(seen) > MAX_TEXT_LEAVES:
        log.info("evidence: %d text predicates exceeds cap; skipping", len(seen))
        return None

    for veto in plan.vetoes:
        plan.veto_by_column.setdefault(veto.column, veto)

    plan.positives.sort(
        key=lambda p: (
            SOURCE_ORDER.index(p.column)
            if p.column in SOURCE_ORDER
            else len(SOURCE_ORDER)
        )
    )
    return plan


def _lit(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _matches(leaf: TextLeaf) -> str:
    return f"REGEXP_LIKE(COALESCE({leaf.column}, ''), {_lit(leaf.pattern)})"


def _admitted(plan: EvidencePlan, pos: TextLeaf) -> str:
    veto = plan.veto_by_column.get(pos.column)
    if veto is None:
        return _matches(pos)
    return f"({_matches(pos)} AND NOT {_matches(veto)})"


def build_evidence_columns(plan: EvidencePlan) -> str:
    source_arms = "\n    ".join(
        f"WHEN {_admitted(plan, p)} THEN {_lit(SOURCE_LABEL.get(p.column, p.column))}"
        for p in plan.positives
    )
    span_arms = "\n    ".join(
        f"WHEN {_admitted(plan, p)} THEN REGEXP_EXTRACT({p.column}, {_lit(p.pattern)})"
        for p in plan.positives
    )
    # Without a diagnosis axis there is no other way in, so an unmatched row
    # cannot be labelled diagnosis_code.
    source_else = _lit("diagnosis_code") if plan.has_dx_axis else "NULL"

    if plan.vetoes:
        neg_arms = "\n    ".join(
            f"WHEN {_matches(v)} THEN REGEXP_EXTRACT({v.column}, {_lit(v.pattern)})"
            for v in plan.vetoes
        )
        negated = f"CASE\n    {neg_arms}\n    ELSE NULL\n  END"
    else:
        negated = "CAST(NULL AS VARCHAR)"

    if plan.has_dx_axis and plan.vetoes:
        any_positive = " OR ".join(_matches(p) for p in plan.positives)
        any_admitted = " OR ".join(_admitted(plan, p) for p in plan.positives)
        contradicted = f"CASE WHEN ({any_positive}) AND NOT ({any_admitted}) THEN true ELSE false END"
    else:
        contradicted = "false"

    return (
        f"  , CASE\n    {source_arms}\n    ELSE {source_else}\n  END AS ev_source\n"
        f"  , CASE\n    {span_arms}\n    ELSE NULL\n  END AS ev_span\n"
        f"  , {negated} AS ev_negated_span\n"
        f"  , {contradicted} AS ev_contradicted\n"
    )


def _outer_from_offset(sql: str) -> int | None:
    """Offset of the outer query's FROM.

    Tokenised so a FROM inside a string literal, CTE or subquery cannot be
    mistaken for it.
    """
    try:
        tokens = sqlglot.tokenize(sql, dialect=DIALECT)
    except Exception:
        return None
    depth = 0
    for token in tokens:
        if token.token_type == TokenType.L_PAREN:
            depth += 1
        elif token.token_type == TokenType.R_PAREN:
            depth -= 1
        elif token.token_type == TokenType.FROM and depth == 0:
            return token.start
    return None


def rewrite(sql: str, plan: EvidencePlan) -> str | None:
    try:
        offset = _outer_from_offset(sql)
        if offset is None:
            return None
        head, tail = sql[:offset], sql[offset:]
        out = f"{head.rstrip()}\n{build_evidence_columns(plan)}{tail}"
        # Re-parse so a splice landing in the wrong place fails here, not at Trino.
        sqlglot.parse_one(out, dialect=DIALECT)
        return out
    except Exception:
        log.warning("evidence: rewrite failed; using original sql", exc_info=True)
        return None


def with_evidence(sql: str) -> tuple[str, bool]:
    """`(sql_to_run, has_evidence)`. Always returns runnable SQL."""
    plan = build_plan(sql)
    if plan is None:
        return sql, False
    rewritten = rewrite(sql, plan)
    if rewritten is None:
        return sql, False
    return rewritten, True
