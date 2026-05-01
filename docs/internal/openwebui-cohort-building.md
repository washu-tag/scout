# OpenWebUI Cohort Building

## Goal

Develop the cohort building process in OpenWebUI Chat, building on lessons learned from [xnat-poc-implementation-plan.md](./xnat-poc-implementation-plan.md).

## Background

After the demo of this branch, we broke the cohort building process into Query, Refinement, Review, and Submit. We thought Review needed a dedicated UI because OWUI Action modals and Rich UI have limited interactivity. We were wrong. This testing showed that a separate Review UI is neither simple nor lovable (though the OWUI UI still has its limitations). The handoff between chat and a separate Review UI is clunky and disjointed. The OWUI Action modal is sufficient for final submission confirmation, and we can live with the limitations of the chat interface for query refinement and result review.

I tested a handoff path: store a submission JSON in MinIO, "Send to XNAT" opens a link to a Scout Viola playbook reading that JSON from MinIO. It worked technically, but it has the same UX problem as OWUI modals. The user can't easily go back to chat for refinement and the flow felt disjointed so better to live with what we have in OWUI's chat interface.

The LLM is great at patient criteria and SQL generation. It is bad at "drop reports X/Y/Z" individual row decisions, which is exactly what a Review UI was meant to support. But users likely don't need to click through a 100s of cases. Showing a few is enough to verify the LLM's search criteria. If something's wrong, the user will need to tell the chat.

So the Review UI is dropped. We retain OWUI Action modals for final submission confirmation, same as the POC.

New from the POC: I ported negation filtering from the cohort building playbook into the Scout query tool. Useful and important for text-based criteria and we will also want to highlight positive vs negated matches in the UI. 

We also want to answer questsions regarding OWUI file handling and cleanup, and the LLM's interaction with query results vs files with the new workflow. Answers to those questions are below.

## Process

### 1. Query and Refine (chat, conversational loop)

- User describes cohort in natural language
  - "Find mentions of lymphoma (especially NHL), specifically DLBCL or follicular lymphoma grades 1 to 3"
- LLM runs queries (structured plus text search with negation filtering)
  - SQL targeting report text, diagnosis codes, demographics, etc.
  - Negation filter automatically excludes "no evidence of", "history of", "ruled out", etc. via per-match context check
  - LLM receives a small sample of results to validate quality
- User sees results with dynamic Rich UI (table UI for results, sample report cards)
- User refines criteria iteratively
  - Add/remove diagnosis codes, narrow modality, facility, date range, demographics
  - Spot-check a handful of sample reports to validate filtering quality within the chat interface

### 2. Package and Submit (chat, submission file generation)

- User clicks "Submit to XNAT" action
- OWUI Action gathers metadata (project name, IRB number, etc.)
- Submission file created with final cohort (CSV or JSON with metadata)
- OWUI Action modal handles final review/edit of submission metadata and confirms send to XNAT

### 3. Manage Submissions

- Out of scope of this POC, but we will have an XNAT driven Scout UI for managing submissions.

## Questions and Answers

### How does the LLM interact with query results and result files? And for Refinement can this be driven by the existing result file, or will this be a new query? Do we need to edit/update the result file?

The LLM should interact with query results, not files. Files are for display and export only. Pumping report data through chat context fills the window fast and risks hallucination. Round-tripping changes from chat back into a file ("drop report X, Y, Z") is awkward. Easier to add data to context than to extract structured edits from a reply. The LLM should have some control over how much data it requests instead of a hard row limit. Also the report text data is what we need to limit to the LLM in some way not all query results. Refinement is driven by new queries, not edits to an existing file. Queries are fast and the chat bogs down when fed too much report data.

### How do we clean up files?

Cleanup is left to OWUI site admins. Options to consider:

1. Cron-job / k8s job similar to [prune-open-webui](https://github.com/Classic298/prune-open-webui). Would require an admin account with OWUI outside of KC similar to the Grafana setup.
2. Keep last N files per chat, cleanup on every action. Simple, but abandoned files linger if a chat is deleted. Could also do some combination of this and #1, keep last N files plus a periodic cleanup.
3. Store files in MinIO, use bucket lifecycle policies. I was able to send files to MinIO from an OWUI Action when testing a Review UI handoff. So we could use MinIO at some point in the flow if we think it adds values. We could store all query result files in MinIO, we could store only submission files in MinIO which could be important to preserve, or we could skip MinIO entirely.

Reference threads: [OWUI #12091](https://github.com/open-webui/open-webui/discussions/12091), [OWUI #23380](https://github.com/open-webui/open-webui/discussions/23380).

## Next Steps

- Quick look into OpenCodeAI for Agentic capabilities.
- Working with excel/csv files. A user either brings a file with patient IDS / EPIC MRNs or the user downloads the query results CSV, edits / refines it, and re-uploads it for submission to XNAT. 
