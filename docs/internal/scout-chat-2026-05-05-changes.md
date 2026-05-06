# Scout Chat — Cohort-Building Iteration (2026-05-05)

A working journal of one day's iteration on the Scout cohort-building chat
POC, layered on top of [`scout-tool-design.md`](./scout-tool-design.md) (the
original three-tool design from earlier in the spike). New chats picking up
from here should treat `scout-tool-design.md` as the foundation and this
doc as the diff.

## TL;DR

| Area | Before today | After today |
|---|---|---|
| Negation classifier | medspaCy ConText (broken: numpy ABI mismatch on OWUI image) | Pure-stdlib regex with sentence scoping, section stripping, termination cues |
| Public param: cohort handle | `in_cohort` (search_reports) + `result_id` (read_reports) — two names, same value | `cohort_id` everywhere |
| Public param: text-search hint | `negation_search_patterns` | `positive_terms` |
| ICD-bypass | `bypass_negation_column` parameter (3 coupled SQL pieces) | Removed; ICD codes go directly in WHERE via `any_match(diagnoses, ...)` |
| Cohort handle format | UUID4 (36 chars) | `coh_<6 url-safe chars>` (10 chars, self-identifying prefix) |
| Saved-cohort surfacing | Buried mid-paragraph in summary | Leads the LLM-bound summary; visible chip in the iframe toolbar |
| Display modes | `none / count / breakdown / table` (4 modes) | `none / table` (2 modes) — aggregates render in chat as prose/markdown |
| LLM context from `read_reports` | Could exceed 70KB for 10-report fetches (full text per row) | Capped at 1 report's full text + identifier-with-why-excerpt for the rest |
| Table row UX | Plain rows | Included rows tinted green ✓, excluded tinted red ✗, included sorted to top |
| LRU cohort retention | 3 (caused chained queries to fail mid-conversation) | 10 |
| System prompt | ~10K tokens, rules-first | ~7.4K tokens, examples-first (4 worked examples up top) |
| Tool requirements | medspaCy + spaCy + numpy install via `requirements:` frontmatter | Empty — no pip installs at Tool load |

End state on dev02: demo-ready for tomorrow with both gemma4-e31b and
qwen3.5:35b; qwen3.5 produces noticeably better narrative prose around the
tool calls.

## Why these changes

### medspaCy out, regex in

We started the day chasing better negation accuracy via medspaCy ConText
(per `scout-tool-design.md` §9 Phase 8). Got blocked on a numpy 1.x vs.
numpy 2.x ABI mismatch in the OWUI 0.9.2 base image: pandas/scipy were
compiled against numpy 2.x, spaCy 3.7.5 wheels against numpy 1.x. Fix
options were (a) bake a custom OWUI image with Python 3.12 + a relaxed
medspaCy fork, or (b) try `--no-deps` install of spaCy 3.8 alongside
existing OWUI runtime. Both fragile. Decision: rip medspaCy/spaCy entirely
and improve the original regex with three additions over the 50-char
window:

1. **Sentence scope** — split on `.!?`, classifier looks at the whole
   sentence containing each match.
2. **Section stripping** — drop `HISTORY` / `INDICATIONS` / `TECHNIQUE`
   / `COMPARISON` sections before scanning. Most "evaluation for PE"-style
   false positives live in non-finding sections.
3. **Termination cues** — `but / however / except / although` close
   negation scope mid-sentence ("no focal mass, but PE in the right
   pulmonary artery" → PE included).

Plus four-axis labeling (positive / negated / uncertain / historical) for
each match. Pure stdlib `re`, no dependencies. 8/8 unit-test cases
including the `"PET-CT was performed to evaluate suspected PE"` false
positive pass.

When demand for better assertion classification grows, the right answer is
either a Python 3.12 OWUI base image (medspaCy works there per its
`requirements/requirements.txt` conditional) or a separate microservice.
Don't reach for medspaCy on the current 0.9.2 image — see how today's day
went.

### Naming pass — `cohort_id`, `positive_terms`, `coh_<6>` IDs

`scout-tool-design.md` proposed `result_id` as the persistent handle and
`in_cohort` as the parameter name on `search_reports` (predicate-shaped:
"find rows that are in this cohort"). The LLM kept tripping over the
two-name, one-value design. Renamed to `cohort_id` everywhere — both the
returned handle and the parameter on both tools. Researcher-domain term,
universally understood.

`negation_search_patterns` was actively misleading — the LLM repeatedly
tried to pass actual negation phrases ("no PE") into it. Renamed to
`positive_terms` — the value is now self-documenting.

UUID4 IDs were 36 chars and looked like noise. Switched to
`coh_<6 url-safe chars>` (e.g. `coh_aB3zX9`) — Stripe-style typed prefix.
Self-identifying (LLM and humans both know what `coh_` is), 10 chars
total, ~57B combinations (collision-free at our scale).

### `bypass_negation_column` removed

The original parameter let the LLM pass a boolean column name; rows where
it was true bypassed the assertion classifier. Required three coordinated
SQL pieces (computed alias column + OR-extended WHERE + parameter value)
that the LLM kept getting wrong. Replaced with prompt guidance: when the
user asks about a clinical concept (PE, MI, COPD, etc.), combine text
search with `any_match(diagnoses, d -> d.diagnosis_code LIKE 'I26%')`
directly in the WHERE clause. The diagnoses array is clinician-validated
ICD coding — a stronger signal than text matching for established
concepts. Removes 30 lines from the prompt and ~50 lines of plumbing from
the Tool.

### Display modes trimmed to `none` and `table`

Original four modes (`none / count / breakdown / table`) plus
`detail`-on-`read_reports`. The chat itself renders aggregate breakdowns
beautifully as markdown tables; we don't need a custom HTML bar chart for
the model to "use". Cut to two:

- `display="none"` — aggregates. Model summarizes in chat. Smaller payload.
- `display="table"` — row-level cohort searches. Iframe with cohort_id
  chip + ✓/✗ row tinting + sort-included-first.

Lost: the bar chart looked nice. Gained: less for the LLM to choose
between, less rendering code to maintain, less HTML payload per message.

### `read_reports` LLM-context discipline

`read_reports(max_reports=10)` was packing all 10 reports' full text into
the LLM message — saw 73KB messages on dev02. The previous fix capped at
8KB total budget. Better fix this round (after Andy noticed it was still
too much): only the **first** report goes into the LLM as full text.
Every other row is a one-line identifier (`mci · modality · service ·
date · age · sex · INCLUDED/EXCLUDED`) plus the cohort's
`why_included` / `why_excluded` excerpt (already on the artifact, free).

User still sees all 10 reports as cards in the iframe. This forces the
model to focus on one narrative at a time and use the assertion
classifier's snippets to reason about the rest. If the model wants to
read another specific report it has to call `read_reports` again with
that `message_control_id` — which is the right pattern.

Constant: `MAX_LLM_READ_FULL_TEXT_REPORTS = 1`.

### iframe vs. markdown — a recurring tension

Andy raised: "OWUI shows 'Ask' / 'Explain' buttons when text is selected
in chat — could we use that on Scout reports?"

Real tradeoff:
- iframe → rich UI, **and** content stays out of LLM context on
  follow-up turns (per OWUI's rich-ui docs)
- markdown in message body → OWUI selection UI works, **but** flooding
  context every turn — exactly what we just fixed

Punted. The iframe stays. If the demand for highlight-and-ask grows, the
right path is custom in-iframe "Quote to chat" buttons via postMessage
(not trivial — OWUI doesn't expose chat-input injection cleanly), or an
upstream OWUI feature request to make `embeds` participate in selection
UI. Don't move report rendering to markdown; the context-isolation
property of the iframe is non-negotiable for `read_reports` detail mode.

A memory has been saved to flag this decision so future iterations don't
re-litigate.

### Prompt restructured examples-first

Per Anthropic's tool-use guide, examples beat rules for smaller models.
Old prompt was rules-first with examples as one-liners at the bottom.
Flipped: 4 worked examples at top (aggregate, cohort+classifier, verify,
upload+chain), then the schema cheat sheet, then Trino quirks +
time-filter table, then concise tips. Cut from ~10K to ~7.4K tokens.

The diagnoses-array nudge specifically calls out: when the user asks
about a well-known clinical concept, combine `LOWER(report_text) LIKE`
with `any_match(diagnoses, ...)`. Saw qwen3.5 generalize this from the PE
example to a brand-new "cerebral infarction in 2024" query (used I63%
ICD codes correctly without prompting).

The prompt also now explicitly tells the model: when row-level SQL filters
on `report_text` with `LIKE`, ALSO pass `positive_terms` — the classifier
needs them to flag negation. Without it, `LIKE '%pulmonary embolism%'`
returns rows that say "no evidence of pulmonary embolism" with no way to
distinguish them.

## Verified flows on dev02

- **Flow A — aggregate**: "How has CT chest report volume changed
  year-over-year since 2020?" → `display="none"`, prose summary with
  6-row markdown table. ~5 tool calls including service-name discovery
  (qwen3.5 was methodical, found `'CT THORAX'` is the actual exam name).
- **Flow B — cohort with classifier**: "Find brain MR reports from 2024
  with cerebral infarction findings." → qwen3.5 wrote SQL combining text
  variants + `any_match(diagnoses, d -> d.diagnosis_code LIKE 'I63%')`,
  saved cohort, classifier correctly distinguished
  `"effectively excluding acute cerebral infarction"` (excluded) from
  `"Acute right parietal cerebral infarction"` (included).
- **Flow C — CSV upload + chain**: upload `test_patients.csv` (5 real
  EPIC MRNs + 1 fake) → `load_id_list()` returns `cohort_id`, surfaces
  matched/unmatched → `search_reports(cohort_id=...)` chains in with
  `{{cohort}}` placeholder. Both gemma4-e31b and qwen3.5 ran end-to-end.

## Files to deploy

The Tool / Function bodies and the prompt all live under
`ansible/roles/open-webui/files/`:

- `scout_query_tool.py` (76.7K, version 0.5.0) — `search_reports` +
  `read_reports`
- `scout_id_list_tool.py` (~26K, version 0.2.0) — `load_id_list`
- `xnat_export_action.py` (~33K) — Send-to-XNAT Action
- `gpt-oss-scout-query-prompt.md` (~7.4K) — Scout Explorer system prompt

Plus deploy artifacts:

- `stroke_research_cohort.csv` (repo root) — 25-row demo CSV (5 real
  EPIC MRNs + 20 synthetic)
- `docs/internal/scout-chat-demo-plan.md` — 5-minute demo playbook

Deploy steps for prod (`chat.scout.washu.edu`) per the demo plan:

1. Admin → Tools → `scout_query_tool` → paste → Save
2. Admin → Tools → `scout_id_list_tool` → paste → Save
3. Admin → Functions → `xnat_export_action` → paste → Save
4. Admin → Models → Scout Explorer → System Prompt → paste → Save
5. (Optional) Tools → scout_query_tool → Valves → set
   `owui_files_lru_keep` to 10 if it's overridden lower

**Caveat**: backward-compat code for old recipe shapes
(`negation_patterns` recipe key, `included_in_cohort` row flag) was
ripped out today. Any saved cohorts created before today's deploy will
fail to chain. Tell colleagues to start fresh chats post-deploy.

## Things to look at next

### From Andy's notes today

**Download CSV from chat.** Right now if the user wants the matched-IDs or
the saved-cohort row list as a file, they can't easily get it. The
`Send to XNAT` button covers the export-to-DICOM path but not the
"give me the row list as CSV for SAS / R" path. Earlier in the spike
we shipped a `Download CSV` button inside the iframe; it was removed
because the base64-blob href bloated the chat history. Need to re-add it
but via a different mechanism — probably a download-only OWUI Action
that re-reads the saved cohort artifact and serves it as a CSV stream.
About 50 lines.

**Embedded CSV was too much.** When `load_id_list` displays the upload
card, the unmatched-IDs list rendered inline got large for big uploads.
Same general theme as the iframe-payload story — need a per-section
char cap on the inline render for the load_id_list card too. Or move
the unmatched list behind a "show all 200 unmatched" disclosure so the
default render is one-screen.

**Occasional websocket failures.** Andy noticed the chat sometimes drops
mid-response. Suspects rich-UI iframe payloads pushing the OWUI
websocket past a frame-size limit. Worth investigating:
1. Check if it correlates with iframe size — a 200-row table iframe
   produces a ~100KB embed. If the websocket frame cap on dev02 is
   smaller than that, we'd need to chunk.
2. Look at `MAX_RENDERED_TABLE_ROWS = 50` and
   `MAX_RENDERED_DETAIL_CARDS = 20` — could lower these without
   functional regression.
3. Check OWUI's `WEBSOCKET_REDIS_URL` config; Valkey may need a tuning
   knob for max-message-size.

### From `scout-tool-design.md` we still owe

- Phase 4 — `xnat_export_action.py` was trimmed today (removed
  `included_in_cohort` legacy fallback) but the picker UX still shows
  raw filenames; design proposal in chat was Option 2 (infer
  `cohort_id` from the assistant message the button was clicked on).
  Picked but not implemented.
- Phase 7 — verification on real Trino with realistic cohort sizes
  (we tested with synth data: ~16 PE-mentioning reports per year, etc.).
- Resolved-MRN view dependency (§7 of the design) — `resolve_to_current`
  is still a no-op until the sibling branch lands.

### Bigger-picture: agentic-tools survey

[`agentic_tools.md`](./agentic_tools.md) is the open question of whether
we're leaving meaningful agentic capability on the table by sticking with
OWUI's primitives + custom Tools. Andy wants to start evaluating those
candidates tomorrow. The doc lays out three buckets:

1. Stay on OWUI, push primitives further (today's work is the latest
   instance of this — examples-first prompts, explicit context
   discipline, etc.).
2. Add an agentic component alongside OWUI.
3. Replace OWUI for something better for clinical-researcher UX.

Worth re-reading that doc with today's context: many of today's pain
points (the SQL-iteration loop, the verification workflow, the "I just
told you which cohort" smell on Send-to-XNAT) are exactly what agentic
loops are designed to solve. The honest question is whether OWUI 14.x's
Tool + Action surface is enough or whether we need something with
better state-tracking and tool-chaining primitives.

### Open for tomorrow's discussion

- Should `cohort_id` get a friendlier user label (e.g. "Cohort #2 in
  this chat") in the iframe chip and summary? Andy raised this; we
  punted. Easy to do as a UI-only pass without changing the ID format.
- Does the demo plan hold up on prod data? The CSV (`stroke_research_cohort.csv`)
  was built from dev02's 5 real EPIC MRNs; prod will have different IDs.
- gemma4-e31b vs. qwen3.5:35b as Scout Explorer default — qwen3.5 is
  measurably better at presentation and SQL. gemma4 is faster. For the
  demo prefer qwen3.5; for routine usage either works.

## How to pick this up tomorrow

1. Read this doc.
2. Skim [`scout-tool-design.md`](./scout-tool-design.md) §1–§5 for the
   foundation (the architectural pieces that didn't change).
3. Look at [`scout-chat-demo-plan.md`](./scout-chat-demo-plan.md) for the
   four-act walkthrough you'll be running in the demo.
4. Skim [`agentic_tools.md`](./agentic_tools.md) — that's the
   eval-other-frameworks track Andy wants to start tomorrow.

The codebase is in `ansible/roles/open-webui/files/` if you need to
trace a behavior to source. Nothing has been committed yet — all
changes are uncommitted on the `awl-cohort-building-chat-poc` branch.
