Proposed new table, *report_patient_mapping*, with columns:

| column                    | description                                                                                                               |
|---------------------------|---------------------------------------------------------------------------------------------------------------------------|
| scout_patient_id          | Reconciled ID to provide to the end user via join.                                                                        |
| primary_report_identifier | ID used to match to curated report table. Foreign key for `primary_report_identifier` in curated table.                   |
| mpi                       | Legacy MPI from any possible source, direct or inferred.                                                                  |
| epic_mrn                  | EPIC MRN for the patient, direct or inferred.                                                                             |
| consistent                | Boolean defining if the IDs connected to the patient are transitively consistent. Set to true unless specified otherwise. |

Proposal: this proposal describes the addition of a new delta lake table, `report_patient_mapping` to allow tracking a patient longitudinally.
When processing a report in the curated table, we'll first look at the `primary_report_identifier` derived for the report. If the
`primary_report_identifier` already exists in `report_patient_mapping`, we've already processed this report before, so we will skip
processing it here any further. Otherwise, we will first consolidate some patient IDs from the report based on version:
* HL7 2.3: mpi [curated table] -> mpi [report_patient_mapping]
* HL7 2.4: First non-null ID in (bjh_ee, bjwc_ee, slch_ee) -> mpi
* HL7 2.7: empi_mr -> mpi
* HL7 2.7: epic_mrn [curated table] -> epic_mrn [report_patient_mapping]
* HL7 2.7: mbmc_mr -> epic_mrn
The justification equating 2.3 mpi to 2.7 empi_mr is based on explicit guidance within the clinical environment. Equating `ee` IDs to `mpi`
and `mbmc_mr` to `epic_mrn` is based on empirical evidence that they seem to match (with reasonable rates of DOB and name matches) within
the dataset. After this process, a 2.3 or 2.4 report should have a consolidated `mpi` and a 2.7 report should have a consolidated `epic_mrn`
and possibly a consolidated `mpi` as well.

The guiding principle in this algorithm is that performing these operations on a single report at a time is an expensive operation,
so we can have better performance by handling as many reports in bulk operations as possible, even at the expensive of algorithmic
complexity. When creating a new value for `scout_patient_id`, we always do so as a new random UUID.

Stage 1: In stage 1 we do a preprocessing step to split up the incoming reports. The main idea is that if we're handling a report and already
have a mapping with the exact IDs, we can copy that row with only the `primary_report_identifier` changed to that of the incoming report. In
this scenario, we have not gained any new information, so we don't need to worry about more complex transitive ID match issues. However, if we
had already determined that the exact match had consistency issues, we will retain that for the new report. This should also be the most common
scenario because of addenda that trigger several new report messages and follow-up studies in the same HL7 epoch, so we can short-circuit more
complex processing often. The way this works is we take all reports that we can defer until the end to handle in 1 bulk operation as defined by:
1. Any incoming report with an exact match (both `mpi` and `epic_mrn`) in the mapping table already, OR
2. Any incoming report with duplicated IDs among the rest of the incoming reports _except_ the first report.
We split these reports into a `deferred_reports_df` to process simply at the end after we have resolved the rest of the reports. The steps below
operate on the remaining reports in the incoming batch.

Stage 2: now in stage 2, we are left with a subset of the incoming batch such that:

* Every report incoming has a unique ID combination
* There are no exact matches of ID combinations between the incoming reports and the mapping table (or themselves)

The most common situation for one of these reports is that there's no match at all. We filter the remaining reports to only those that, with respect
to `mpi` and `epic_mrn`:
1. Are fully disjoint from the existing mapping table AND
2. Are fully disjoint from the rest of the incoming reports

There is no information here to bridge reports or detect any problems, so we will create a new "consistent" row in the mapping table for each of the reports.
This is what will happen for new patients (or a clean database). It can also happen in bulk, keeping the process quick.

Stage 3: now in stage 3, we are left with an even more refined dataset where:

* Every report incoming has a unique ID combination
* There are no exact matches of ID combinations between the incoming reports and the mapping table (or themselves)
* Every report in the incoming batch must have a partial ID match to another report, either in the batch or already in the mapping table

From this point, we know there is at least one match, and it must be a partial match. There are two difficult problems we must address. Consider
an example that looks like this:


| scout_patient_id | primary_report_identifier | mpi | epic_mrn | consistent |
|------------------|---------------------------|-----|----------|------------|
| SCOUT_1          | REP_1                     | M_1 |          | true       |
| SCOUT_2          | REP_2                     |     | EPIC_1   | true       |

and suppose we get a new report (REP_3) in with a consolidated `mpi` of "M_1" and consolidated `epic_mrn` of "EPIC_1". Not only should we already be aware of this patient,
but getting this third reports provides the missing link to show that the known "SCOUT_1" and "SCOUT_2" are actually the same patient. So, we will pick the first of these IDs
and overwrite the value in `scout_patient_id` so that each of these rows is equal. Our table now looks like:

| scout_patient_id | primary_report_identifier | mpi | epic_mrn | consistent |
|------------------|---------------------------|-----|----------|------------|
| SCOUT_1          | REP_1                     | M_1 |          | true       |
| SCOUT_1          | REP_2                     |     | EPIC_1   | true       |
| SCOUT_1          | REP_3                     | M_1 | EPIC_1   | true       |

Suppose now we get a new report (REP_4) in with a consolidated `mpi` of "M_1" and consolidated `epic_mrn` of "EPIC_2". This represents a data integrity issue where we cannot
properly handle the IDs anymore. As such, we want to mark _all_ of the connected reports as incosistent to exclude them from the join to provide IDs. For such reports, any
patient ID should be considered unreliable. After processing such a report, our table might look like:

| scout_patient_id | primary_report_identifier | mpi | epic_mrn | consistent |
|------------------|---------------------------|-----|----------|------------|
| SCOUT_1          | REP_1                     | M_1 |          | false      |
| SCOUT_1          | REP_2                     |     | EPIC_1   | false      |
| SCOUT_1          | REP_3                     | M_1 | EPIC_1   | false      |
| SCOUT_1          | REP_4                     | M_1 | EPIC_2   | false      |


This becomes especially difficult because this linking of reports needs to be done _recursively_. That is, consider the scenario that when we received `REP_4`, our
patient mapping table looked as follows:

| scout_patient_id | primary_report_identifier | mpi | epic_mrn | consistent |
|------------------|---------------------------|-----|----------|------------|
| SCOUT_1          | REP_1                     | M_1 |          | true       |
| SCOUT_1          | REP_2                     |     | EPIC_1   | true       |
| SCOUT_1          | REP_3                     | M_1 | EPIC_1   | true       |
| SCOUT_2          | REP_A                     | M_A | EPIC_2   | true       |
| SCOUT_2          | REP_B                     | M_A |          | true       |

In this scenario, not only is `REP_A` now inconsistent with `EPIC_2` being tied to differing MPIs, but by extension `REP_B` is also inconsistent *even though
it contains no identifiers in common with the received report*.

Now, let's return to our process. We can split the remaining reports on if only one of `mpi` and `epic_mrn` is non-null in our incoming report. If true, we will not
have any new information to declare IDs inconsistent or bridge reports, so we can handle these here in bulk. For reports with a partial match in the mapping table
already, we will create a new row from our report, inheriting the `scout_patient_id` and `consistent` flag of the partial match. Otherwise, we're guaranteed to have
a partial match to another report in the incoming batch, so we can simply create a new row in the mapping table, and if the `consistent` flag needs to be set to
`false` later, that will happen within the scope of processing the other report.

Stage 4: to summarize what we know about each report we're left with in this stage:
* Every report incoming has a unique ID combination
* There are no exact matches of ID combinations between the incoming reports and the mapping table (or themselves)
* Every report in the incoming batch must have a partial ID match to another report, either in the batch or already in the mapping table
* Every report in the incoming batch has both a non-null `mpi` and `epic_mrn`

At this point we do a recursive search to find all linked reports, row by row. The expectation is that this will not be particularly fast, but that we have already
narrowed down the scope of the reports to process such that there will not be a significant time problem. For each report left, we do the following:
1. Search both the remaining reports and mapping table by `mpi` and `epic_mrn` to find the partial matches.
2. For each of the partial matches, perform the same search if any new `mpi` or `epic_mrn` values are uncovered.
3. Recurse as far as necessary to build the full patient "web".

With this bolus of report mappings, if there is still only a single non-null value for `mpi` and a single non-null value for `epic_mrn`, the data is all still
consistent. In this case, we will overwrite all values for `scout_patient_id` with the first value already in the mapping table (or create one if none), and _copy_ any such
modified rows to a `report_patient_mapping_history` table  for provenance if needed. Otherwise, we have inconsistent patient IDs, so we wish to mark _all_ such reports as
inconsistent so they can be excluded. This is done by again copying rows that will be modified to `report_patient_mapping_history` and overwriting `consistent` to "false".
The incoming report will also have a `scout_patient_id` assigned to an arbitrary ID from the search. The history backups are the main form of "logging" for this process.
Either nothing is logged by the transformer, or only a generic message about inconsistencies found to avoid intentionally adding PHI into the logs.

Stage 5: now we bring back the `deferred_reports_df` that we set aside previously. Each row is guaranteed to have an exact match, so we can duplicate the existing exact
matches for each report along with the already determined `scout_patient_id` and `consistent` flag.

---

Pros:
1. The ID resolution process does not touch the base tables (reports or curated reports), which reduces risk of concurrency issues.
2. Design is relatively easy to adapt to a process that runs on demand or scheduled, disconnected from the main ingest if we change our minds.
3. Even in a hopefully rare scenario where we need a sort of "patient split" operation that's the opposite of a patient merge at some later date,
that could be implemented in a way that only touches the mapping table and leaves the reports/curated tables intact.
4. Leaving the curated table as-is means we don't have to worry about mapping changes propagating further to the latest/dx tables.
5. Inconsistent data is explicitly surfaced rather than silently wrong.

---

Discarded:

Name and DOB searches: when we search the existing report by these IDs and compare names and birth dates, there is a reasonably high exact equal matches to suggest
that using these IDs is reasonable to begin with. However, there are a pretty significant number of mismatches where the report contains a "different" name that from
a spot check perspective look like "probably" the same patient. Examples (similar to what I have seen in the real data) for my name could include:
1. CHARLES MOORE
2. C MOORE
3. CHERLES MOORE
4. CHARLES MOORE-SMITH

This is to say that any type of fuzzy match seems impossible to implement, or would in the end just come down to a guess. Is "CHARLES MOORE" the same as "CHARLES MOORE-SMITH"? Well,
yes if I have gotten married, but probably not otherwise. Even for DOBs with patient IDs matching, there were not too infrequent inconsistencies. To make the name problem even worse,
I saw frequent examples (to use Kate's name as an example) of "patient names" like "KATESBOY ALPERT" or "KATESGIRL ALPERT". Based on those odd "names" and the source, I am inferring
that they are patient names in reports for newborns. The scenario would be either parents not yet decided on a name for their child and this is a placeholder, or maybe for some reason
a different department of the hospital hasn't gotten the memo yet. So could this be used as a "tiebreaker" in the case of inconsistent IDs? I would say _barely_ in the extent that
it would work in some scenarios, but a tiebreaker that you can't trust isn't off much use. If we already suspect the data we have around a patient(s) is bad, adding an unreliable
indicator won't help.

SSNs: using SSNs as an attempt to match patients over time has a theoretical problem that not every patient even has an SSN (e.g. newborns, international visitors). In practice,
our data seems similarly incomplete. Out of the roughly 4,000,000 2.4 reports, around 630,000 of them do not have an SSN. In addition, even when it is populated, Pan's analysis has
showed the data contains consistency problems as well. This leads me to the same conclusion as the names question that the data is inconsistent and sparse, and so would
not work well as a tiebreaker.
