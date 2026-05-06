# Scout Chat Tool Design

**Status:** Draft for team review.
**Date:** 2026-05-04.
**Audience:** Scout engineers (the proposal, the implementation) and
clinical-research stakeholders who want to know what the chat will
feel like (the tool inventory and the flows).
**Scope:** the OWUI Tools and Actions that power the cohort-building
chat experience. The Kubernetes file-abstraction layer is covered in
[`file-abstraction-research.md`](./file-abstraction-research.md); the
agent-loop and Bedrock decisions are in
[`agentic_tools.md`](./agentic_tools.md). Both are out of scope here.

## TL;DR

Three Tools, organized around a search/read split: **`search_reports`**
finds reports by criteria and returns metadata plus short snippets;
**`read_reports`** pulls full findings and impression for specific
report versions; **`load_id_list`** turns an uploaded CSV/Excel of
patient or accession identifiers into a cohort the LLM can constrain
searches against. **Send to XNAT** stays as an OWUI Action
(user-clicked, not LLM-callable). Persisted artifacts are tiny — a
recipe (the SQL plus filters) plus a list of identifiers — not the
full row data, which stays in Trino. No new datastore, no cache. The
whole system is one OWUI Tool file plus a small shared module,
callable by an LLM running on Ollama or Bedrock without changing the
rest of Scout.

## 1. Why this doc exists

The cohort-building POC currently exposes one OWUI Tool — the
~870-line Scout Query Tool — that bundles SQL execution, negation
filtering, server-side persistence, and five rendering modes behind a
single `query()` function. As the team adds capabilities (uploaded
patient lists, follow-up read flows, an eventual
`resolved_patient_id` / `resolved_epic_mrn` Trino view that's still
on a sibling branch), three design questions are forcing decisions:

1. **Is one mega-tool the right shape, or do we decompose?**
2. **Does patient-list upload extend the existing tool or warrant a
   new one?**
3. **Is "search for reports" different from "read individual
   reports," and how do we keep the LLM from drowning in report text
   while still letting it actually read findings?**

Today's tool answers these implicitly and unevenly. This doc proposes
explicit answers, grounded in published guidance and healthcare
precedent.

## 2. The proposal: three Tools

### A note on identifiers and which table to query

Scout publishes two relevant tables (see
[Downstream Tables](https://washu-scout.readthedocs.io/en/latest/dataschema.html#downstream-tables)):

- **`delta.default.reports`** — the curated silver table. Contains
  every HL7 message: preliminaries, finals, addenda, corrections.
  One accession can have multiple rows.
- **`delta.default.reports_latest`** — derived from `reports`, kept
  to the most recent message per accession by `message_dt`. One row
  per accession in the common case, with the documented caveat that
  some multi-part studies have a "preliminary" sibling that gets
  dropped.

**`search_reports` and `load_id_list` default to `reports_latest`.**
That's the right default for cohort building — researchers usually
want the final read, not every interim version, and the
deduplication by-accession sidesteps a class of footguns. The base
`reports` table stays available; the SQL is the LLM's choice. The
system prompt should nudge the model toward `reports_latest` unless
the user explicitly asks for historical versions.

About identifiers in the resulting cohorts:

- **`message_control_id`** (HL7 MSH-10) is the per-row primary key
  in either table. Even in `reports_latest`, one row → one
  `message_control_id`. Tools that act on specific report versions
  (e.g., `read_reports`) key on this.
- **`accession_number`** is the natural grouping identifier. In
  `reports_latest`, accessions are already de-duplicated at table
  level so `message_control_id` and `accession_number` are
  effectively in 1:1 correspondence per row. The XNAT Action takes
  the artifact's de-duped `accession_numbers` list to build its
  payload — XNAT thinks at the accession level.

Artifacts store both lists. Tools accept either as input. The
distinction is mostly invisible to the LLM until someone explicitly
queries `reports` instead of `reports_latest`, at which point
preliminary-vs-final reasoning matters.

### `search_reports` — find reports by criteria

```python
async def search_reports(
    sql: str,
    display: str = "table",                  # none | count | breakdown | table
    in_cohort: Optional[str] = None,         # result_id of a saved cohort
    snippet_around: Optional[str] = None,    # regex; include ±1 sentence
    negation_search_patterns: Optional[list[str]] = None,
    bypass_negation_column: str = "",
    llm_context_rows: Optional[int] = None,
    __user__: Optional[dict] = None,
    __chat_id__: str = "",
    __event_emitter__: Optional[Callable] = None,
) -> Any
```

The search half. Runs SQL — defaulting to `delta.default.reports_latest`,
falling back to `delta.default.reports` when the user explicitly
wants historical versions. Returns to the LLM whatever metadata
columns the SQL `SELECT`-s — `accession_number`, `message_control_id`,
`epic_mrn`, `modality`, `service_name`, `service_identifier`,
`message_dt`, `requested_dt`, `observation_dt`, `principal_result_interpreter`,
`diagnoses`, `patient_age`, `sex`, `race`, `report_status`,
`study_instance_uid`, anything else useful from the schema. The LLM
gets all of it. The *only* columns stripped from LLM-bound rows are
the giant text ones — `report_text`, `report_section_findings`,
`report_section_impression`, `report_section_addendum`,
`report_section_technician_note`. When `snippet_around` is set, the
tool extracts a sentence-sized snippet around each regex hit so the
LLM can scan for relevance without loading whole reports.

**No `display="detail"` here.** Detail rendering needs full text, and
this tool deliberately doesn't have it. If the user wants to read
specific reports, that's what `read_reports` is for — the five render
modes split naturally between the two tools (`search_reports` does
`none` / `count` / `breakdown` / `table`; `read_reports` does `none` /
`detail` / `table`).

`in_cohort` is the chaining hook: pass a `result_id` from a previous
`load_id_list` or `search_reports` call. **The LLM is responsible for
placement** — it must include the literal token `{{cohort}}` in its
SQL `WHERE` clause where the cohort filter belongs. The tool
substitutes `{{cohort}}` with `message_control_id IN ('...', ...)` (or
`epic_mrn IN ('...', ...)`, depending on which identifier columns
the cohort artifact carries) before sending to Trino. The LLM never has to inline 1,247 IDs in its
context; the tool never has to parse SQL.

```sql
-- Row-level search:
SELECT message_control_id, accession_number, modality, message_dt
FROM reports_latest
WHERE service_name LIKE '%CHEST%' AND {{cohort}}

-- Aggregate over a cohort (placeholder goes inside the CTE):
WITH cohort_reports AS (
  SELECT * FROM reports_latest WHERE {{cohort}}
)
SELECT modality, COUNT(*) FROM cohort_reports GROUP BY 1
```

If `in_cohort` is set and `{{cohort}}` doesn't appear in the SQL,
the tool errors loudly rather than running an unfiltered query.
Placement is the LLM's job because only the LLM knows the SQL's
structure; substitution is the tool's job because it has the values.
No clever AST manipulation, no silent breakage on subqueries / CTEs
/ joins.

`negation_search_patterns` keeps the existing 12-regex / 50-character
context window for "no evidence of," "ruled out," etc., and the
`bypass_negation_column` escape for ICD-coded matches. The regex list
is shared with the XNAT Action via a `scout_negation.py` module so
the patterns live in exactly one place.

**Negation context flows back to the LLM.** Today's tool tells the
model only the count of excluded rows. That's not enough — when a
researcher asks "why did 47 reports get excluded?" the LLM should be
able to answer. So every excluded LLM-bound sample row carries a
**`negation_excerpt`** field — the matched phrase plus ±50 chars of
context (`"...history denied. No evidence of pulmonary embolism.
Aorta..."`). The model can then explain *what* triggered each
exclusion, not just *how many* — important when researchers want to
verify the negation patterns matched the right thing.

Aggregates (`SELECT modality, COUNT(*) ... GROUP BY 1`) are still
searches; they just don't get persisted, because there are no
identifier columns to chain or export.

### `read_reports` — full text for specific reports

```python
async def read_reports(
    message_control_ids: Optional[list[str]] = None,  # specific versions
    accession_numbers: Optional[list[str]] = None,    # latest/final per
    result_id: Optional[str] = None,
    sections: list[str] = ["findings", "impression"],
    display: str = "detail",                          # detail | table | none
    max_reports: int = 5,
    sample: str = "first",                            # first | random
    included: Optional[bool] = None,                  # True/False/None=both
    __user__: Optional[dict] = None,
    __chat_id__: str = "",
) -> str
```

The read half. Returns full report sections for up to `max_reports`
reports. Three equally first-class entry points:

- **Specific report versions.** Pass `message_control_ids=[...]` to
  read exactly those rows — the right call when you care about a
  preliminary vs. final read.
- **Latest version per accession.** Pass `accession_numbers=[...]`
  and the tool returns the most recent / final report for each.
  This is the natural shape when a researcher names accessions and
  doesn't care about prior versions.
- **Sampled from a cohort.** Pass `result_id` plus `max_reports`,
  `sample`, and optional `included` (`True` for included rows only,
  `False` for excluded only, `None` for both) to draw reports from
  the saved identity list — useful for spot-checks like "3 random
  included + 3 random excluded" after a search.

`display="detail"` (the default) renders the full report cards in
the iframe. `display="table"` renders a comparative table when a
researcher wants to scan multiple reports' impressions side-by-side
without the full findings text. `display="none"` skips the iframe
when the LLM just wants text in context.

The cap matters. `read_reports` defaults to `max_reports=5`, so a
misjudged call costs at most a handful of reports in context. A
deliberate `max_reports=50` is a visible choice.

### `load_id_list` — turn an upload into a cohort

```python
async def load_id_list(
    __files__: list[dict] = [],            # OWUI's auto-injected uploads
    id_column: Optional[str] = None,        # explicit override
    id_type: str = "auto",                 # epic_mrn | mpi | accession_number
                                           # | message_control_id | auto
    resolve_to_current: bool = True,        # only meaningful for patient IDs
    __user__: Optional[dict] = None,
    __chat_id__: str = "",
) -> str
```

Takes whatever the user attached to the chat (CSV, XLSX, TSV) via
[OWUI's `__files__` injection](https://github.com/open-webui/open-webui/discussions/14773),
parses it server-side, normalizes the ID column (auto-detect or
explicit), validates against Scout, and returns a `result_id`. The
artifact carries whatever identifier the upload supplied (`epic_mrn`
for patient lists, `accession_number` or `message_control_id` for
report lists). Whichever it is plugs straight into `search_reports`'s
`in_cohort` parameter — the search tool reads the artifact and
infers the substitution column from what's there.

For initial implementation, the tool keys patient cohorts on
`epic_mrn` directly — no MPI resolution. A future
`resolved_patient_id` / `resolved_epic_mrn` Trino view is being
built on a sibling branch; when it lands, `resolve_to_current=True`
will join through that view so a researcher uploading a list of
old MPIs from a past study gets back a cohort keyed on current
Epic MRNs without needing to learn about the resolution step.
Until then, the parameter is a no-op and the tool emits a one-line
notice if the upload contains identifiers that look like stale
MPIs.

Following the [Stanford STARR Tools pattern](https://med.stanford.edu/content/sm/starr-tools/data-delivery/lists.html),
unmatched IDs are surfaced as a triage list ("213 of 1,247 IDs were
not found — review them?") rather than silently dropped. The LLM gets
a one-line summary; the user sees the breakdown in the iframe. Render
output is a single confirmation card with stats plus the unmatched
list — there's no `display` parameter, just the one shape that
makes sense.

**Why this is its own tool, not `search_reports(from_file=...)`.** It
is genuinely tempting to fold uploads into `search_reports` and skip
a tool call. We didn't because the validate-and-triage step is the
load-bearing UX: if a researcher uploads a misformatted file or a
list with stale IDs, hiding that inside a search means the LLM
silently runs over a partial cohort. Stanford STARR's explicit
"unmatched IDs surface for review before use" pattern is the right
shape, and it requires the upload step to be its own visible action.
The cost is one extra tool call per upload session, paid once per
chat.

### Send to XNAT stays an Action

`xnat_export_action.py` continues to be an OWUI Action — a button
attached to a chat message, not a Tool the LLM invokes. That's the
right primitive for a destructive, external side effect: the user
clicks; the LLM doesn't autonomously decide to ship a cohort to XNAT.
The Action becomes a *consumer* of `result_id` artifacts: it picks
the most recent search-derived cohort scoped to the chat, takes its
identity list, re-queries Trino for the metadata it needs to build
the payload, and POSTs.

The Action's flow is now intentionally short: file picker (only if
multiple recent cohorts in the chat) → project/IRB form → confirm
→ POST → result. The "review individual reports" step that earlier
versions of the Action had — showing samples of included/excluded
reports inside the Action modal — is **removed**. Review happens in
the chat itself: the LLM calls `read_reports` on positive and
negative samples for the researcher to verify before they ever
click Send to XNAT. By the time a researcher hits the button, the
cohort has already been audited; the Action's job is just IRB
metadata + ship.

The Action uses OWUI's `__event_call__` mechanism (request/response,
blocks until the user responds, 300 s timeout — distinct from
`__event_emitter__`'s fire-and-forget status updates).

## 3. What this looks like in chat

Three flows that map to real research scenarios. Each shows the tool
calls inline — that's what an engineer sees in logs, and the rough
outline of what an end-user reads in chat.

### Flow A — explore the data (no chain, no persistence)

A new study coordinator wants a sense of CT chest volume to plan
recruitment.

> **User:** "How has CT chest volume changed year over year since 2020?"
>
> **LLM** → `search_reports(sql="SELECT year, COUNT(*) AS n FROM`
> `delta.default.reports_latest WHERE modality='CT' AND service_name LIKE`
> `'%CHEST%' AND year >= 2020 GROUP BY year ORDER BY year",`
> `display="breakdown")`
>
> No identifier columns in the result → no persistence. The iframe
> shows a bar chart and the LLM summarizes the trend in one sentence.

### Flow B — build a cohort, verify, export (the hot path)

A radiologist preparing a follow-up study on incidental thyroid
nodules wants every neck CT report from 2024 that mentions a nodule
≥ 1 cm with no documented follow-up imaging.

> **User:** "Find all CT neck reports from 2024 that mention a thyroid
> nodule of at least 1 cm, and exclude anything with documented
> follow-up imaging or known malignancy."
>
> **LLM** → `search_reports(sql=<SELECT against reports with regex on`
> `report_section_findings for nodule size>, snippet_around="thyroid`
> `nodule", negation_search_patterns=["follow-up imaging", "follow up`
> `imaging", "known malignancy", "thyroid cancer"], display="table")`
>
> 312 reports match the positive criteria; 47 are excluded by the
> negation regex. The LLM gets `message_control_id`, `accession_number`,
> modality, dates, the sentence-around-nodule snippets, and a
> `negation_excerpt` for each excluded sample row showing what
> triggered the exclusion. The user sees the full table inline with
> the negation flags visible.
>
> **User:** "Show me 3 random included and 3 random excluded so I
> can verify the negation."
>
> **LLM** → `read_reports(result_id="abc123", included=True,`
> `sample="random", max_reports=3)` and again with `included=False`.
>
> Six full impressions come back. The radiologist confirms the regex
> caught what they wanted.
>
> **User clicks Send to XNAT.** The review already happened in chat,
> so the Action skips straight to the project/IRB form. It picks
> `abc123`, takes the de-duplicated `accession_numbers` from its
> identity (XNAT thinks at the accession level, not at
> per-report-version), re-queries Trino for the export metadata,
> walks the project/IRB modal, confirms, and POSTs.

This flow is what the rest of the design is shaped around.

### Flow C — upload a patient list, find their imaging

A coordinator running a prostate cancer registry has 847 patients in
an Excel file and needs their pelvic MR history.

> **User:** *uploads `prostate_registry.xlsx`* "Find all MR pelvis
> reports for these patients."
>
> **LLM** → `load_id_list(__files__=[<the upload>],`
> `id_type="epic_mrn")` → `result_id="def456"`, 847 IDs (12 unmatched
> and surfaced for review). (Once the resolved-MRN view lands on the
> sibling branch, `resolve_to_current=True` will also remap any old
> MPIs to current Epic MRNs; for now the upload is keyed straight
> on `epic_mrn`.)
>
> **LLM** → `search_reports(sql="SELECT message_control_id,`
> `accession_number, modality, service_name, message_dt FROM`
> `delta.default.reports_latest WHERE service_name LIKE '%MR%PELVIS%'`
> `AND {{cohort}}", in_cohort="def456")`
>
> The tool substitutes `{{cohort}}` with `epic_mrn IN ('<847 IDs from
> def456>')` before running. The LLM never sees the MRN list — that's
> the point of the cohort handle. 1,234 MR pelvis reports come back.
>
> Coordinator clicks Send to XNAT.

`def456` is load-bearing throughout: without it, the LLM would have
to either inline 847 MRNs in every SQL query (context blowup) or
guess what the file contained.

## 4. Why this shape

### The search/read split is the load-bearing decision

Anthropic's
[*Writing tools for agents*](https://www.anthropic.com/engineering/writing-tools-for-agents)
(Sept 2025) names the pattern explicitly: a `search_logs` tool that
returns matching lines plus surrounding context, paired with a
`read_logs` tool for full content. Production systems that have
landed on this exact split as of 2026:
[Notion MCP](https://developers.notion.com/docs/mcp) (search returns
page IDs + titles + snippets; fetch returns full body),
[GitHub MCP and Vercel Grep](https://vercel.com/blog/grep-a-million-github-repositories-via-mcp)
(code search returns filename + line range + snippet around the hit),
[Slack MCP](https://docs.slack.dev/changelog/2026/02/17/slack-mcp/)
(message previews in search; full thread on follow-up). The
healthcare analog is
[AWS HealthLake MCP](https://aws.amazon.com/blogs/industries/building-healthcare-ai-agents-with-open-source-aws-healthlake-mcp-server/)'s
`search_resources` versus `patient_everything`.

The single near-universal refinement to "search returns metadata
only" is that production search tools return *windowed snippets* —
a sentence or two around the hit — so the agent can decide whether
to drill in. Pure-metadata search is rare. Scout's `search_reports`
follows this: text columns are stripped from LLM context by
default, but `snippet_around` opts into "give me ±1 sentence around
each regex hit" so the LLM has enough to triage without seeing whole
reports.

### Why three (and not five, or one)

The first draft of this doc proposed five Tools (a separate
`apply_negation_filter`, a separate `fetch_reports_for_cohort`, etc.).
Both got removed during review:

- **`apply_negation_filter`** would have de-duplicated the 12-pattern
  regex shared with the XNAT Action. But the right place to fix code
  duplication is a code module, not a Tool surface — extracting
  `scout_negation.py` solves it without making the LLM learn a
  separate step. Tool surfaces shouldn't reflect every code-reuse
  opportunity.
- **`fetch_reports_for_cohort`** would have searched reports
  constrained to a saved patient cohort. Folded into `search_reports`
  via the `in_cohort` parameter, since the alternative was two tools
  that did nearly the same thing — exactly the fragmentation
  Anthropic warns against. (And [OpenAI's function-calling
  guide](https://developers.openai.com/api/docs/guides/function-calling)
  separately recommends combining sequentially-called tools.)

Three Tools sits comfortably inside the
[5–8 per-server sweet spot](https://www.mcpbundles.com/blog/mcp-tool-design-pattern)
the MCP community converged on, with headroom for the additions in
§8 if usage data justifies them.

### Decision framework

Distilled from the research, useful for future tool questions:

- **Combine** when two operations are nearly always called in
  sequence, share most arguments, or differ only in rendering or in
  how many rows come back.
- **Combine** when the second operation is just "the first with a
  `WHERE` clause" — that's a parameter, not a tool.
- **Split** when inputs are fundamentally different (SQL vs. an
  uploaded file vs. an explicit ID list).
- **Split** when the LLM would otherwise reproduce business logic
  in every call (e.g., constructing 847-element IN clauses).
- **Split** when cost profiles diverge sharply — a metadata fan-out
  has very different defaults from a full-text drill-down. Folding
  them costs the LLM a high-stakes judgement call on every
  invocation, and the silent failure mode (text leaks into a
  metadata call) is exactly what we're avoiding.
- **Always** prefer description quality over granularity. A
  well-documented tool with one clear example beats two narrow tools
  with vague docstrings.

## 5. What we save (and what we don't)

### Recipe + rows, not full data

Every `search_reports` or `load_id_list` call that produces a
chainable result writes a small JSON artifact to OWUI Files. The
artifact has two pieces: a `recipe` (the inputs that produced the
result, useful for traceability and debugging) and `rows` (the
identifiers and per-row flags).

For a `search_reports` result:

```json
{
  "recipe": {
    "sql": "SELECT message_control_id, accession_number, modality, message_dt FROM delta.default.reports_latest WHERE ... AND {{cohort}}",
    "negation_patterns": ["no evidence", "ruled out"],
    "bypass_negation_column": null,
    "snippet_around": "thyroid nodule",
    "in_cohort": "def456"
  },
  "rows": [
    {"message_control_id": "abc", "accession_number": "A123", "included": true},
    {"message_control_id": "def", "accession_number": "A124", "included": false,
     "why_excluded": "no evidence of pulmonary embolism"}
  ]
}
```

For a `load_id_list` result (patient list):

```json
{
  "recipe": {
    "source_file": "prostate_registry.xlsx",
    "id_column": "EPIC_MRN",
    "id_type": "epic_mrn"
  },
  "rows": [{"epic_mrn": "1234567"}, {"epic_mrn": "2345678"}],
  "unmatched": ["9999999"]
}
```

For a `load_id_list` result (accession list): same shape with
`id_type: "accession_number"` and `accession_number` in the rows.

That's the whole protocol. The `recipe` is what the tool actually
ran (the literal SQL with `{{cohort}}` still in it, the negation
config, any `in_cohort` reference for lineage). The `rows` are the
data. Things deliberately left out: no `kind` (derivable from which
identifier columns appear in `rows[0]`), no `schema_version`, no
`row_count`, no aggregate flag-count summaries — anything derivable
from `rows` stays derived. The substitution logic for `{{cohort}}`
in a downstream search picks the most-specific identifier column it
finds in `rows[0]` (`message_control_id` > `accession_number` >
`epic_mrn`).

**Yes, we save the negated rows** — every row the SQL matched is in
`rows`, with `included: false` and a `why_excluded` snippet for the
ones the negation regex dropped. That makes the cohort auditable:
the LLM can come back later with `read_reports(result_id=...,
flag="excluded", sample="random")` and read excluded reports for a
spot-check, and the user can ask "what got excluded?" days later.
The XNAT Action filters to `included: true` rows when building its
payload. Saving excluded rows costs a few dozen extra entries — a
few KB at typical cohort sizes.

**Not in the artifact:** the row data, the report text, the
metadata. That all lives in Trino. When something — the XNAT
Action, a `read_reports` sample, an `in_cohort`-constrained search —
needs the rows, it re-runs the query.

For a 50,000-row cohort, the size comparison:

| Option                       | Size       |
|------------------------------|------------|
| Full row data (current naïve)| ~50 MB     |
| Recipe + identity (proposed) | ~400 KB    |
| Recipe alone                 | ~1 KB      |

We save the bottom two.

**When do we save vs. skip?** The trigger is *column presence in the
result*, not the display mode. Persist when the result includes
`message_control_id` and/or `accession_number` (or `epic_mrn` for
patient cohorts). If a query is `SELECT modality, COUNT(*) FROM ...
GROUP BY 1` — no row identifiers come back — there's nothing for a
downstream tool to chain or export, so we don't burn an OWUI File on
it. The LLM gets the totals in its summary, the iframe shows the
bar chart, end of story. The system prompt should nudge the model
to always include `message_control_id` and `accession_number` in
row-level `SELECT`s; if it doesn't, the result is treated the same
way as an aggregate.

### No cache. Real numbers.

Artifact writes go through OWUI's Files persistence layer (whatever
backend OWUI is configured to use). For the cohort sizes we expect:

| Cohort size     | Recipe + identity | Round-trip per Tool | 4-Tool chain |
|-----------------|-------------------|----------------------|--------------|
| 200 rows        | ~2 KB             | ~30 ms               | ~120 ms      |
| 2,000 rows      | ~20 KB            | ~50 ms               | ~200 ms      |
| 20,000 rows    | ~200 KB           | ~150 ms              | ~600 ms      |
| 200,000 rows   | ~2 MB             | ~500 ms              | ~2 s         |

For comparison: a single LLM tool-call decision in our setup is
5–30 s, and a real Trino query is 1–30 s. The artifact I/O is at
worst a rounding error on the LLM and Trino latency we're already
paying. **Build without a cache. Measure. Add only if we observe
pain.** File cleanup / lifecycle is a separate discussion (the
existing `owui_files_lru_keep` Valve plus chat-scoped `meta` tags
gives us the basic levers; tuning is in §10).

### How a `result_id` is actually used downstream

Concrete plumbing, since this is the part that's most often asked
about:

**Production by `search_reports`.** The Tool runs the SQL, applies
negation, captures `message_control_id` and `accession_number` from
the result rows, builds the recipe + rows JSON, and writes it to
OWUI Files via `Files.insert_new_file()` tagged with
`meta.scout_result=True, meta.chat_id=<chat>`. The returned
`result_id` is just the OWUI file ID. The same pattern applies to
`load_id_list`, with the recipe carrying the upload's source
filename and parsed-ID stats instead of an SQL string.

**Consumption by `search_reports(in_cohort=...)`.** When a later
search is called with `in_cohort="def456"`:

1. Tool calls `Files.get_file_by_id("def456")` and reads the JSON.
2. Tool inspects `rows[0]` to pick a substitution column —
   `message_control_id` if present, otherwise `accession_number`,
   otherwise `epic_mrn`. (Most-specific wins.)
3. Tool checks the user's SQL for the literal token `{{cohort}}`.
   If missing, errors loudly — `in_cohort` set without a placeholder
   is almost always a model mistake.
4. Tool collects the chosen column's values from `included: true`
   rows (or all rows if `included` isn't a field, as on patient-
   list uploads), and string-replaces `{{cohort}}` with
   `<column> IN ('val1', 'val2', ...)`. No AST work; the
   substitution is on a single token the LLM put exactly where it
   wanted the filter.
5. The substituted SQL runs in Trino. The LLM never sees the IN
   list.

For very large cohorts (>~5000 IDs), the substitution renders as a
`message_control_id IN (SELECT id FROM (VALUES ('...'), ...) AS
t(id))` shape rather than a flat `IN (...)` literal — same end
result, friendlier to Trino's query planner. Implementation detail,
not a Tool contract change; the LLM still just writes `{{cohort}}`.

**Consumption by `read_reports(result_id=...)`.** Similar to above:
load JSON, filter rows by the `included` parameter, sample per
`max_reports` / `sample`, then issue a small `SELECT ... FROM
reports_latest WHERE message_control_id IN (<sampled>)` to fetch
the requested sections. The 5-row default cap keeps these queries
small.

**Consumption by the XNAT Action.** When the user clicks "Send to
XNAT" on a chat message:

1. The Action queries OWUI Files for `meta.scout_result=True` files
   scoped to the chat (`meta.chat_id` match), ordered by recency.
2. If exactly one is recent, use it; otherwise present a picker.
3. Read JSON, take the de-duplicated `accession_numbers` list (XNAT
   speaks in accessions, not in per-version IDs).
4. Re-query Trino for any extra metadata XNAT needs in the export
   payload (study date, modality, patient demographics) keyed by
   accession.
5. Walk the project / IRB / confirm modal via `__event_call__` and
   POST the payload.

The Action never reads row data from the artifact — only the
identity list. That's why we don't need to save row data anywhere:
both the export build and any in-cohort search re-derive what they
need from Trino at use time.

### When the LLM should reach for `in_cohort` vs. just write new SQL

A fair concern is "isn't every chained call another file read?" Most
of the time the answer is no — the LLM can just write fresh SQL.

- **Refining a prior search** (add a year filter, narrow modality,
  change a regex). The LLM has the prior SQL in chat context — it
  just modifies the WHERE clause and reissues. No `in_cohort`, no
  file read. The Tool produces a fresh artifact for the new
  result; that's it.
- **Different aggregation over the same base set.** Same as above —
  rewrite the SQL.
- **An uploaded patient list.** This is the case where `in_cohort`
  is genuinely necessary. The LLM cannot regenerate the patient
  list — it came from a file the LLM never saw. Without
  `in_cohort` (and the `{{cohort}}` placeholder), the LLM would
  have to inline the MRNs, which is the context blowup we're
  preventing. **One file read per `search_reports` call against
  the upload.**
- **Refining a search constrained to an upload.** Each refining
  call still uses the same uploaded-list `result_id`, so the file
  read is repeated — but it's a small artifact (the identity list)
  and the cost is sub-100 ms per call, dwarfed by the LLM and
  Trino latency.

In practice, file reads happen **once per call that uses
`in_cohort`**, which mostly means "once per refinement against an
upload." Search-derived chains can usually skip the file by
modifying SQL directly. The persistence and the chaining mechanism
exist for the upload case and the XNAT export case; everything
else is gravy.

A `result_id` is just the OWUI file ID of the artifact, tagged with
`meta.scout_result=True` and `meta.chat_id=<chat>`. No separate
registry, no parallel datastore, no in-process state. The existing
Query Tool's `_save_to_owui_files()` pattern carries over unchanged
— we just shrink what gets written. Tagging makes artifacts cheap
to find ("most recent scout_result for this chat_id") and easy to
sweep without touching user uploads. Artifacts stay ≤ ~1 MB at
typical sizes, so the existing `owui_files_lru_keep` Valve handles
lifecycle without strain.

### What this assumes

- HL7 reports are append-only (true for Scout): re-querying the same
  identifiers later produces the same data.
- When the user clicks Send to XNAT, the Action runs one fresh
  Trino query keyed on the saved accession_numbers to fetch the
  metadata XNAT needs in the export payload (study date, modality,
  patient demographics). That single query takes single-digit
  seconds for a few hundred or few thousand accessions. We assume
  that's acceptable click-time latency — it is, but if measurement
  shows otherwise the alternative is to save the metadata at
  search time. Today we don't, because the saved-then-stale
  problem is worse than a few seconds at click.
- `accession_number` and `epic_mrn` are durable identifiers we can
  re-key on. (Once the resolved-MRN view lands, that adds
  resolution but doesn't change this assumption.)

## 6. On coupling

Three Tools that share a `result_id` protocol are coupled. A
reasonable team member can push back: "if three tools all have to
know about the same protocol, are they really three tools?" The
honest answer is that **coupling is unavoidable; we're choosing
where it lives.** The alternatives:

- **One mega-Tool with a `mode` parameter.** Massive parameter
  space, internal switch statements, one wrong parameter kills the
  whole call. The LLM still has to learn the modes — it just learns
  them as parameter values instead of tool names, with worse error
  recovery.
- **Tools with no shared state.** The LLM has to copy thousands of
  patient IDs between calls. This is exactly the context-blowup
  this whole design is fleeing — unworkable.
- **Tools sharing a small protocol module.** Each tool's
  *implementation* is independent; only the protocol is shared.
  Errors stay localized. This is the same shape as a REST API
  contract or a database schema — familiar, not quirky.

The proposal puts the coupling in a principled place: a shared
`scout_artifacts.py` module for artifact read/write helpers,
`scout_negation.py` for the regex list. Future tools added in §8
will use the same modules — coupling is contained, not viral. The
test for any new Tool: *what does it do that an existing Tool's
parameter can't do?* If the answer is unclear, it's a parameter.

## 7. Implementation notes

### File layout

```
ansible/roles/open-webui/files/
├── scout_query_tool.py         # search_reports + read_reports
├── scout_id_list_tool.py       # load_id_list
├── scout_negation.py           # 12-regex list, helpers (shared)
├── scout_artifacts.py          # result_id read/write helpers
└── xnat_export_action.py       # consumer of result_id artifacts
```

`search_reports` and `read_reports` live in the same file because
they share the artifact reader, the negation module, and most of
the Trino client setup. `load_id_list` is a separate file because
its upload-parsing path (CSV/XLSX reading, ID-column detection,
unmatched-ID triage) is distinct enough to keep apart.

### OWUI plumbing worth knowing

- **`__files__`** is auto-injected by OWUI when the user attaches
  files to the chat turn. Each entry has `id`, `file.id`,
  `file.data.content`, etc.
  ([discussion #14773](https://github.com/open-webui/open-webui/discussions/14773)).
  No chat-context lookup needed — this is the canonical OWUI 14.x
  pattern.
- **`__event_emitter__`** is fire-and-forget — emit `status` /
  `notification` / `citation` events for UI feedback while the
  Tool runs. Use this for the "Querying Trino…" status during a
  long search.
- **`__event_call__`** is request-response and blocks until the
  user responds (300 s timeout). Use this for any Tool that needs
  the user to confirm or fill a form. The XNAT Action uses it for
  the multi-step project/IRB modal.
- **Tools live in Workspace > Tools** (per-chat / per-model
  attach), Functions/Filters/Actions live in Admin Panel >
  Functions. The Scout Explorer model toggle in Admin Panel >
  Models gates Tool availability.
- **MCP support landed in OWUI v0.6.31** (Streamable HTTP only;
  use [`mcpo`](https://github.com/open-webui/mcpo) for stdio). The
  recommendation hasn't flipped — for a single-cluster on-prem
  deployment without cross-user credential needs, native OWUI
  Tools are still simpler than an MCP server. Revisit if Jupyter
  agents or the staging cluster ever want the same tools.

### Filter migration (already landed for OWUI 14.x)

The `stream` hook on Filters has been async since OWUI 0.5.17; the
0.9.0 migration just adds `await` to internal DB calls that became
coroutines. Today's link-sanitizer Filter is already correct after
the recent changes. No new Filter work falls out of this design.

## 8. Future tool candidates (deferred until justified)

Three to consider, ordered by likelihood of adding:

- **`describe_schema(table)`** — returns column list, types, and
  sample-value distributions for `delta.default.reports_latest` and the
  resolved view. Cheap, prevents hallucinated column names.
  Mitigate over-calling with a docstring hint and tool-side
  caching.
- **`combine_cohorts(result_ids, operation)`** — set ops between
  cohorts (union / intersect / difference). Useful when researchers
  build "include from query A *and* the uploaded list B, *minus*
  cohort C." Today this requires raw SQL gymnastics across
  different cohort sources.
- **`lookup_codes(system, query)`** — ICD-10 hierarchies, modality
  groupings, service-name lookups. Could also be a Trino reference
  table that `search_reports` joins against — defer the Tool form
  until the LLM proves it can't write the lookup as SQL on its own.

Explicitly **not** adding: `search_report_text` (overlaps with
`search_reports`'s `snippet_around`), `get_patient_history`
(`search_reports` with a single-MRN `WHERE` covers it),
`export_to_csv` (already implicit in iframe download),
`save_named_cohort` / `load_named_cohort` (cross-chat persistence
opens user-permissions and audit-trail questions worth deferring
until a real user asks).

## 9. Negation: a forward-looking note

The 12-regex / 50-character context window inherited from the
Jupyter playbook is fast and explainable, and it's what we ship
with. But the 2025–2026 evidence is unambiguous that rule-based
negation is meaningfully behind transformer-based approaches on
radiology text — a [PMC 2025
study](https://pmc.ncbi.nlm.nih.gov/articles/PMC12092861/) measured
medspaCy at F1 0.49 vs. CAN-BERT at 0.78 on 984 reports, and a [JMIR
Med Inform 2025 GPT-based labeler](https://medinform.jmir.org/2025/1/e68618)
hit F1 0.90 across positive / negative / uncertain on chest X-rays.
The hybrid pattern (rule-based for explainability and speed, LLM
for ambiguous spans, [MedInsight Pro
2025](https://www.researchgate.net/publication/400851160) is one
example) is winning.

Scout doesn't need to switch today. But when researcher feedback
shows precision pain, the upgrade path is **add a transformer-backed
assertion classifier as a fallback for ambiguous spans** —
`medspaCy ConText` for explainability, an optional Llama-based
assertion classifier called via Bedrock or Ollama, results written
into the same per-row `included` and `why_excluded` fields the
artifact already carries. The Tool surface doesn't change; only the
negation module's internals do.

## 10. Open questions for the team

- **`owui_files_lru_keep` policy.** Today's default of 3 was set
  when artifacts were full row data. Raise to 10 now that
  artifacts are tiny — or change the eviction policy to keep the
  most recent result *per lineage*?
- **CSV download for very large cohorts.** Today's iframe embeds
  the CSV inline. Past ~10 MB we should route the download to a
  server-side endpoint that re-queries Trino. Where does the
  cutoff sit and who owns the endpoint?
- **System prompt strategy.** With three Tools — and, more
  importantly, the search/read split and the upload-vs-query
  decision — the LLM needs explicit guidance on when to use which.
  Worth a separate small doc.
- **MCP later, native now?** Recommend native OWUI for the spike;
  revisit MCP packaging only if Jupyter agents or external clients
  end up wanting the same tools. The
  [tool-namespace conflict noted in #16238](https://github.com/open-webui/open-webui/discussions/16238)
  is a real operational concern for shared MCP deployments and
  not yet documented.
- **Do we expose `result_id` to users at all?** Recommend
  internal — researchers don't need to know — unless we add named
  cohort save / load, in which case names become the surface.

## 11. Out of scope (and where to find it)

- **The Kubernetes file-abstraction layer** for downstream compute
  jobs: [`file-abstraction-research.md`](./file-abstraction-research.md).
- **Whether to use Bedrock, opencode, OWUI alone, or something
  else for the agent loop:**
  [`agentic_tools.md`](./agentic_tools.md) — that work decides
  *how* the LLM calls these tools, not *what* tools we expose.
- **Cohort governance, IRB workflows, audit trails.** This doc
  proposes the technical surface; the policy questions are owned
  elsewhere.

## 12. Sources

**Tool design guidance**

- Anthropic, *Writing tools for agents* (Sept 2025): <https://www.anthropic.com/engineering/writing-tools-for-agents>
- Anthropic, *Best practices for tool definitions*: <https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools#best-practices-for-tool-definitions>
- Anthropic, *Advanced tool use*: <https://www.anthropic.com/engineering/advanced-tool-use>
- OpenAI, *Function-calling guide*: <https://developers.openai.com/api/docs/guides/function-calling>
- Block, *Playbook for designing MCP servers*: <https://engineering.block.xyz/blog/blocks-playbook-for-designing-mcp-servers>
- MCP Bundles, *Six-tool pattern*: <https://www.mcpbundles.com/blog/mcp-tool-design-pattern>
- FutureSearch, *Large dataset upload*: <https://futuresearch.ai/blog/mcp-large-dataset-upload/>

**OWUI 14.x specifics**

- OWUI Tools: <https://docs.openwebui.com/features/extensibility/plugin/tools/>
- OWUI Events: <https://docs.openwebui.com/features/extensibility/plugin/development/events/>
- OWUI Rich UI: <https://docs.openwebui.com/features/extensibility/plugin/development/rich-ui/>
- OWUI MCP: <https://docs.openwebui.com/features/extensibility/mcp/>
- `__files__` pattern (discussion #14773): <https://github.com/open-webui/open-webui/discussions/14773>
- MCP namespace conflict (discussion #16238): <https://github.com/open-webui/open-webui/discussions/16238>

**Search/read pairs in production**

- Notion MCP: <https://developers.notion.com/docs/mcp>
- Vercel grep MCP: <https://vercel.com/blog/grep-a-million-github-repositories-via-mcp>
- Slack MCP changelog: <https://docs.slack.dev/changelog/2026/02/17/slack-mcp/>
- AWS HealthLake MCP: <https://aws.amazon.com/blogs/industries/building-healthcare-ai-agents-with-open-source-aws-healthlake-mcp-server/>

**Healthcare / cohort precedent**

- OHDSI Criteria2Query 3.0 (JMIR 2025): <https://medinform.jmir.org/2025/1/e71252>
- Cohort Discovery LLM survey (arXiv 2506.15301): <https://arxiv.org/html/2506.15301v1>
- Stanford STARR Tools — Lists: <https://med.stanford.edu/content/sm/starr-tools/data-delivery/lists.html>
- Atropos Health ChatRWD: <https://www.atroposhealth.com/research-informatics/>
- Bedrock AgentCore healthcare agents: <https://aws.amazon.com/blogs/machine-learning/building-health-care-agents-using-amazon-bedrock-agentcore/>

**Negation in clinical NLP**

- medspaCy: <https://github.com/medspacy/medspacy>
- CAN-BERT vs medspaCy benchmark (PMC 2025): <https://pmc.ncbi.nlm.nih.gov/articles/PMC12092861/>
- GPT-based radiology labeler (JMIR Med Inform 2025): <https://medinform.jmir.org/2025/1/e68618>
- Azure Text Analytics for Health (assertion API): <https://learn.microsoft.com/en-us/azure/ai-services/language-service/text-analytics-for-health/how-to/call-api>
