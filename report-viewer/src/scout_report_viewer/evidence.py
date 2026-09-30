"""Derive match evidence from a saved cohort query.

Parses the saved SQL, finds the `REGEXP_LIKE` predicates testing a report-body
column, and splices `ev_source` / `ev_positive_span` /
`ev_negative_span` onto its SELECT list.

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

# Which source wins when several matched.
SOURCE_ORDER = (
    "report_section_impression",
    "report_section_findings",
    "report_section_addendum",
    "report_section_technician_note",
    "report_text",
)

# Section parsing is heuristic, so which section matched is not a claim we can
# stand behind to a user. Reported as one text axis; SOURCE_ORDER still decides
# internally which column the span is read from.
TEXT_LABEL = "text"

EV_COLUMNS = ("ev_source", "ev_positive_span", "ev_negative_span", "ev_dx_codes")

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
    veto_for: dict[TextLeaf, TextLeaf] = field(default_factory=dict)
    #: parsed-section columns with a positive, i.e. what report_text falls back from
    section_columns: set[str] = field(default_factory=set)
    #: LIKE patterns the query matches diagnosis_code against
    dx_patterns: list[str] = field(default_factory=list)

    @property
    def has_dx_axis(self) -> bool:
        return bool(self.dx_patterns)


def _names_in(node: exp.Expression) -> set[str]:
    """Every column name referenced, qualified or not.

    A qualified reference like `d.diagnosis_code` parses as a Dot of
    Identifiers rather than a Column, so both node types have to be read.
    """
    names = {c.name for c in node.find_all(exp.Column)}
    names |= {i.name for i in node.find_all(exp.Identifier)}
    return names


def _text_column_of(node: exp.Expression) -> str | None:
    # The subject is usually wrapped, e.g. COALESCE(report_section_impression, '').
    hit = _names_in(node) & TEXT_COLUMNS
    return (
        min(hit, key=lambda n: SOURCE_ORDER.index(n) if n in SOURCE_ORDER else 99)
        if hit
        else None
    )


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


def _dx_patterns(where: exp.Expression) -> list[str]:
    """LIKE patterns applied to diagnosis_code, sorted for a stable rewrite.

    Replaces the model-supplied match_diagnoses: these are the codes the query
    actually filters on, not the ones it said it would.
    """
    out: list[str] = []
    for like in where.find_all(exp.Like):
        subject, pattern = like.this, like.expression
        if not isinstance(pattern, exp.Literal) or not pattern.is_string:
            continue
        if "diagnosis_code" in _names_in(subject) and pattern.this not in out:
            out.append(pattern.this)
    return sorted(out)


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


def _enclosing_select(node: exp.Expression) -> exp.Expression | None:
    parent = node.parent
    while parent is not None:
        if isinstance(parent, exp.Select):
            return parent
        parent = parent.parent
    return None


def _sibling_veto(
    node: exp.Expression,
    leaf: TextLeaf,
    nodes: list[tuple[exp.Expression, TextLeaf]],
) -> TextLeaf | None:
    """The veto guarding this positive: nearest negated sibling on the same column.

    `(A AND NOT A_veto) AND (B AND NOT B_veto)` puts each positive next to its
    own veto, so widening out from the innermost enclosing AND finds the right
    one. Keying on column alone would hand B whichever veto came first.
    """
    ancestor: exp.Expression | None = node
    while ancestor is not None:
        if isinstance(ancestor, exp.And):
            within = set(id(n) for n in ancestor.find_all(exp.Expression))
            for other, other_leaf in nodes:
                if (
                    other_leaf.negated
                    and other_leaf.column == leaf.column
                    and id(other) in within
                ):
                    return other_leaf
        ancestor = ancestor.parent
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

    plan = EvidencePlan(dx_patterns=_dx_patterns(where))
    seen: set[TextLeaf] = set()
    nodes: list[tuple[exp.Expression, TextLeaf]] = []
    # find_all, not walk: walk's yield shape changed between sqlglot majors.
    for node in where.find_all(exp.RegexpLike, exp.Anonymous):
        # Only the outer query's own predicates. A column referenced inside a
        # subquery is in scope there, not in the SELECT we splice into.
        if _enclosing_select(node) is not tree:
            continue
        parts = _regexp_like_parts(node)
        if parts is None:
            continue
        leaf = TextLeaf(column=parts[0], pattern=parts[1], negated=_is_negated(node))
        nodes.append((node, leaf))
        if leaf in seen:
            continue
        seen.add(leaf)
        (plan.vetoes if leaf.negated else plan.positives).append(leaf)

    if not plan.positives:
        return None
    if len(seen) > MAX_TEXT_LEAVES:
        log.info("evidence: %d text predicates exceeds cap; skipping", len(seen))
        return None

    for node, leaf in nodes:
        if leaf.negated:
            continue
        veto = _sibling_veto(node, leaf, nodes)
        if veto is not None:
            plan.veto_for.setdefault(leaf, veto)

    def source_rank(leaf: TextLeaf) -> int:
        return (
            SOURCE_ORDER.index(leaf.column)
            if leaf.column in SOURCE_ORDER
            else len(SOURCE_ORDER)
        )

    plan.section_columns = {
        p.column for p in plan.positives if p.column != "report_text"
    }
    plan.positives.sort(key=source_rank)
    plan.vetoes.sort(key=source_rank)
    return plan


def _lit(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _matches(leaf: TextLeaf) -> str:
    return f"REGEXP_LIKE(COALESCE({leaf.column}, ''), {_lit(leaf.pattern)})"


def _admitted(plan: EvidencePlan, pos: TextLeaf) -> str:
    parts = [_matches(pos)]
    veto = plan.veto_for.get(pos)
    if veto is not None:
        parts.append(f"NOT {_matches(veto)}")
    # report_text is the templated fallback, reachable only when no section
    # parsed. Without this guard a HISTORY mention would be reported as the
    # reason for a row the query actually admitted on its diagnosis code.
    if pos.column == "report_text" and plan.section_columns:
        parts.extend(
            f"COALESCE(TRIM({col}), '') = ''" for col in sorted(plan.section_columns)
        )
    return "(" + " AND ".join(parts) + ")"


def build_evidence_columns(plan: EvidencePlan) -> str:
    admitted = [f"({_admitted(plan, p)})" for p in plan.positives]
    any_text = " OR ".join(admitted)

    if plan.dx_patterns:
        matching = " OR ".join(
            f"d.diagnosis_code LIKE {_lit(p)}" for p in plan.dx_patterns
        )
        matched_codes = f"FILTER(diagnoses, d -> {matching})"
        has_code = f"CARDINALITY({matched_codes}) > 0"
        dx_codes = (
            f"ARRAY_JOIN(TRANSFORM({matched_codes}, d -> d.diagnosis_code), ', ')"
        )
    else:
        has_code = "false"
        dx_codes = "CAST(NULL AS VARCHAR)"

    # text_and_code must precede text, which would otherwise swallow it. NULL
    # means nothing we modelled matched, which is honest about a predicate we
    # failed to classify rather than blaming a code that may not have matched.
    arms = []
    if plan.dx_patterns:
        arms.append(f"WHEN ({any_text}) AND {has_code} THEN {_lit('text_and_code')}")
    arms.append(f"WHEN ({any_text}) THEN {_lit('text')}")
    if plan.dx_patterns:
        arms.append(f"WHEN {has_code} THEN {_lit('diagnosis_code')}")
    source = "CASE\n    " + "\n    ".join(arms) + "\n    ELSE NULL\n  END"

    include_arms = "\n    ".join(
        f"WHEN {_admitted(plan, p)} THEN REGEXP_EXTRACT({p.column}, {_lit(p.pattern)})"
        for p in plan.positives
    )
    if plan.vetoes:
        exclude_arms = "\n    ".join(
            f"WHEN {_matches(v)} THEN REGEXP_EXTRACT({v.column}, {_lit(v.pattern)})"
            for v in plan.vetoes
        )
        exclude = f"CASE\n    {exclude_arms}\n    ELSE NULL\n  END"
    else:
        exclude = "CAST(NULL AS VARCHAR)"

    return (
        f"  , {source} AS ev_source\n"
        f"  , CASE\n    {include_arms}\n    ELSE NULL\n  END AS ev_positive_span\n"
        f"  , {exclude} AS ev_negative_span\n"
        f"  , {dx_codes} AS ev_dx_codes\n"
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


_HIT_ROW = "ROW(field VARCHAR, pos INTEGER, len INTEGER, polarity VARCHAR)"


def _hits_for_leaf(leaf: TextLeaf) -> str:
    """Every match of one pattern as ROWs of (field, 1-based pos, length)."""
    col = f"COALESCE({leaf.column}, '')"
    all_matches = f"REGEXP_EXTRACT_ALL({col}, {_lit(leaf.pattern)})"
    polarity = "negative" if leaf.negated else "positive"
    element = (
        f"CAST(ROW({_lit(leaf.column)}, "
        f"REGEXP_POSITION({col}, {_lit(leaf.pattern)}, 1, CAST(i AS INTEGER)), "
        f"LENGTH({all_matches}[i]), {_lit(polarity)}) AS {_HIT_ROW})"
    )
    # sequence(1, 0) counts *down* in Trino, so the empty case needs its own arm.
    return (
        f"IF(CARDINALITY({all_matches}) = 0,"
        f" CAST(ARRAY[] AS ARRAY({_HIT_ROW})),"
        f" TRANSFORM(SEQUENCE(1, CARDINALITY({all_matches})), i -> {element}))"
    )


def highlight_hits_expression(sql: str) -> str | None:
    """A JSON array of every pattern match, or None if there is nothing to mark.

    Trino does the matching, so the offsets come from the same engine that
    selected the rows. Meant for a single-report read, where one extra pass per
    pattern is free; it would be wasteful across a whole cohort.

    Emitted for every text column the viewer can render, not just the one a
    predicate named: the row panel shows `report_text`, and an offset into
    `report_section_impression` means nothing there.
    """
    plan = build_plan(sql)
    if plan is None:
        return None
    leaves: list[TextLeaf] = []
    for leaf in (*plan.positives, *plan.vetoes):
        for column in dict.fromkeys((leaf.column, "report_text")):
            candidate = TextLeaf(
                column=column, pattern=leaf.pattern, negated=leaf.negated
            )
            if candidate not in leaves:
                leaves.append(candidate)
    arrays = " || ".join(_hits_for_leaf(leaf) for leaf in leaves)
    return f"CAST({arrays} AS JSON)"
