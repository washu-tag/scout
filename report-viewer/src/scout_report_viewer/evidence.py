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

EV_COLUMNS = (
    "ev_source",
    "ev_positive_span",
    "ev_negative_span",
    "ev_dx_codes",
    "ev_dx_text",
)

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
    #: blank-section tests the query itself conjoins with a report_text positive
    guard_for: dict[TextLeaf, frozenset[str]] = field(default_factory=dict)
    #: the query's own `any_match(diagnoses, ...)` tests, verbatim
    dx_tests: list[str] = field(default_factory=list)
    #: lambda from the first of those, for listing which codes matched
    dx_lambda: str | None = None

    @property
    def has_dx_axis(self) -> bool:
        return bool(self.dx_tests)


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


def _blank_sections_in(node: exp.Expression) -> set[str]:
    """Text columns `node` requires to be blank, descending conjunctions only."""
    if isinstance(node, exp.Paren):
        return _blank_sections_in(node.this)
    if isinstance(node, exp.And):
        return _blank_sections_in(node.this) | _blank_sections_in(node.expression)
    if isinstance(node, exp.EQ):
        right = node.expression
        if isinstance(right, exp.Literal) and right.is_string and right.this == "":
            return _names_in(node.this) & TEXT_COLUMNS
    return set()


def _blank_section_guard(node: exp.Expression) -> set[str]:
    """Blank-section tests the query conjoins with `node`.

    Read from the predicate rather than assumed: synthesising this guard
    rejects rows the WHERE admitted, which read back as unexplained.
    """
    found: set[str] = set()
    ancestor = node.parent
    # Through NOT too: a veto sits under one, in the same conjunction as the
    # positive it guards.
    while isinstance(ancestor, (exp.Paren, exp.And, exp.Not)):
        if isinstance(ancestor, exp.And):
            found |= _blank_sections_in(ancestor)
        ancestor = ancestor.parent
    return found


def _is_negated(node: exp.Expression) -> bool:
    """Whether an odd number of NOTs encloses `node`.

    `NOT (A AND B)` negates both, so reading only the immediate parent would
    report A as the reason for a row the query admitted for lacking B.
    """
    negated = False
    parent = node.parent
    while parent is not None:
        if isinstance(parent, exp.Not):
            negated = not negated
        parent = parent.parent
    return negated


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
    pattern = pattern_node.this
    # LOWER(x) ~ 'p' is x ~ '(?i)p'. Rewriting it keeps the span and the
    # offsets reading the column as written instead of a folded copy.
    if "(?i" not in pattern and any(subject.find_all(exp.Lower, exp.Upper)):
        pattern = "(?i)" + pattern
    return column, pattern


def _dx_axis(where: exp.Expression) -> tuple[list[str], str | None]:
    """The query's `any_match(diagnoses, ...)` tests and the first lambda.

    Reused verbatim rather than re-derived from the LIKE patterns inside.
    A diagnosis axis may test `diagnosis_code_text`, or several columns at
    once, and rebuilding it from the patterns silently dropped everything
    that was not a bare `diagnosis_code LIKE`.
    """
    tests: list[str] = []
    lam: str | None = None
    for call in where.find_all(exp.Anonymous):
        if call.name.lower() != "any_match" or len(call.expressions) != 2:
            continue
        subject, body = call.expressions
        if "diagnoses" not in _names_in(subject):
            continue
        # An excluded code set admits nothing, so it must not claim a row or
        # supply the lambda that lists which codes matched.
        if _is_negated(call):
            continue
        tests.append(call.sql(dialect=DIALECT))
        if lam is None and isinstance(body, exp.Lambda):
            lam = body.sql(dialect=DIALECT)
    return tests, lam


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


def _conjoined_ids(node: exp.Expression) -> set[int]:
    """Nodes reachable from `node` without crossing a disjunction."""
    if isinstance(node, exp.Paren):
        return _conjoined_ids(node.this)
    if isinstance(node, exp.And):
        return _conjoined_ids(node.this) | _conjoined_ids(node.expression)
    if isinstance(node, exp.Or):
        return set()
    return {id(n) for n in node.find_all(exp.Expression)}


def _sibling_veto(
    node: exp.Expression,
    leaf: TextLeaf,
    nodes: list[tuple[exp.Expression, TextLeaf]],
) -> TextLeaf | None:
    """The veto guarding this positive: nearest negated sibling on the same column.

    `(A AND NOT A_veto) AND (B AND NOT B_veto)` puts each positive next to its
    own veto, so widening out from the innermost enclosing AND finds the right
    one. Keying on column alone would hand B whichever veto came first.

    The walk stops at a disjunction: a veto in the other arm of an OR does not
    constrain this positive.
    """
    ancestor: exp.Expression | None = node
    while ancestor is not None:
        if isinstance(ancestor, exp.Or):
            return None
        if isinstance(ancestor, exp.And):
            within = _conjoined_ids(ancestor)
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
    lowered = sql.lower() if sql else ""
    if "regexp_like" not in lowered and "any_match" not in lowered:
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

    dx_tests, dx_lambda = _dx_axis(where)
    plan = EvidencePlan(dx_tests=dx_tests, dx_lambda=dx_lambda)
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
        if leaf.column == "report_text":
            plan.guard_for.setdefault(leaf, frozenset(_blank_section_guard(node)))

    if not plan.positives and not plan.has_dx_axis:
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

    plan.positives.sort(key=source_rank)
    plan.vetoes.sort(key=source_rank)
    return plan


def _lit(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _matches(leaf: TextLeaf) -> str:
    return f"REGEXP_LIKE(COALESCE({leaf.column}, ''), {_lit(leaf.pattern)})"


def _guard_parts(plan: EvidencePlan, leaf: TextLeaf) -> list[str]:
    """The blank-section conditions the query conjoins with `leaf`.

    The templated report_text arm only applies when no section parsed, so
    dropping it would read a HISTORY mention as the reason for a row the
    query admitted on its impression.
    """
    return [
        f"COALESCE(TRIM({col}), '') = ''"
        for col in sorted(plan.guard_for.get(leaf, ()))
    ]


def _fires(plan: EvidencePlan, veto: TextLeaf) -> str:
    return "(" + " AND ".join([_matches(veto), *_guard_parts(plan, veto)]) + ")"


def _admitted(plan: EvidencePlan, pos: TextLeaf) -> str:
    parts = [_matches(pos)]
    veto = plan.veto_for.get(pos)
    if veto is not None:
        parts.append(f"NOT {_matches(veto)}")
    parts.extend(_guard_parts(plan, pos))
    return "(" + " AND ".join(parts) + ")"


def build_evidence_columns(plan: EvidencePlan) -> str:
    admitted = [f"({_admitted(plan, p)})" for p in plan.positives]
    any_text = " OR ".join(admitted)

    if plan.dx_tests:
        has_code = " OR ".join(f"({t})" for t in plan.dx_tests)
    else:
        has_code = "false"
    if plan.dx_lambda:
        matched = f"FILTER(diagnoses, {plan.dx_lambda})"
        dx_codes = f"ARRAY_JOIN(TRANSFORM({matched}, x -> x.diagnosis_code), ', ')"
        # A code can be admitted by its text, so the code alone does not say
        # why the row is here.
        dx_text = f"ARRAY_JOIN(TRANSFORM({matched}, x -> x.diagnosis_code_text), ', ')"
    else:
        dx_codes = "CAST(NULL AS VARCHAR)"
        dx_text = "CAST(NULL AS VARCHAR)"

    # text_and_code must precede text, which would otherwise swallow it. NULL
    # means nothing we modelled matched, which is honest about a predicate we
    # failed to classify rather than blaming a code that may not have matched.
    arms = []
    if plan.dx_tests and any_text:
        arms.append(f"WHEN ({any_text}) AND ({has_code}) THEN {_lit('text_and_code')}")
    if any_text:
        arms.append(f"WHEN ({any_text}) THEN {_lit('text')}")
    if plan.dx_tests:
        arms.append(f"WHEN ({has_code}) THEN {_lit('diagnosis_code')}")
    source = "CASE\n    " + "\n    ".join(arms) + "\n    ELSE NULL\n  END"

    if plan.positives:
        include_arms = "\n    ".join(
            f"WHEN {_admitted(plan, p)} THEN REGEXP_EXTRACT({p.column}, {_lit(p.pattern)})"
            for p in plan.positives
        )
        include = f"CASE\n    {include_arms}\n    ELSE NULL\n  END"
    else:
        include = "CAST(NULL AS VARCHAR)"
    if plan.vetoes:
        exclude_arms = "\n    ".join(
            f"WHEN {_fires(plan, v)} THEN REGEXP_EXTRACT({v.column}, {_lit(v.pattern)})"
            for v in plan.vetoes
        )
        exclude = f"CASE\n    {exclude_arms}\n    ELSE NULL\n  END"
    else:
        exclude = "CAST(NULL AS VARCHAR)"

    return (
        f"  , {source} AS ev_source\n"
        f"  , {include} AS ev_positive_span\n"
        f"  , {exclude} AS ev_negative_span\n"
        f"  , {dx_codes} AS ev_dx_codes\n"
        f"  , {dx_text} AS ev_dx_text\n"
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


def _hits_for_leaf(plan: EvidencePlan, leaf: TextLeaf) -> str:
    """Every match of one pattern as ROWs of (field, 1-based pos, length)."""
    col = f"COALESCE({leaf.column}, '')"
    all_matches = f"REGEXP_EXTRACT_ALL({col}, {_lit(leaf.pattern)})"
    polarity = "negative" if leaf.negated else "positive"
    element = (
        f"CAST(ROW({_lit(leaf.column)}, "
        f"REGEXP_POSITION({col}, {_lit(leaf.pattern)}, 1, CAST(i AS INTEGER)), "
        f"LENGTH({all_matches}[i]), {_lit(polarity)}) AS {_HIT_ROW})"
    )
    empty = f"CAST(ARRAY[] AS ARRAY({_HIT_ROW}))"
    # sequence(1, 0) counts *down* in Trino, so the empty case needs its own arm.
    hits = (
        f"IF(CARDINALITY({all_matches}) = 0, {empty},"
        f" TRANSFORM(SEQUENCE(1, CARDINALITY({all_matches})), i -> {element}))"
    )
    guard = _guard_parts(plan, leaf)
    if guard:
        hits = f"IF({' AND '.join(guard)}, {hits}, {empty})"
    return hits


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
    if not leaves:
        return None
    arrays = " || ".join(_hits_for_leaf(plan, leaf) for leaf in leaves)
    return f"CAST({arrays} AS JSON)"
