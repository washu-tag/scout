# Test Data Overview

The data in this directory has been generated to check the functionality of the scout platform.
The tests assume that scout has ingested exactly the HL7-ish log files in this directory. Cases to
point out in particular are:

1. Odd report text: [20250814.log](extraction/20250814.log) contains a report with some special characters
added (`±60%÷`) to check that the platform is still extracting the report text correctly. There was a bug
in development for non-ASCII but ISO-8859-1 characters, so that log file has been intentionally encoded
as ISO-8859-1 for regression test coverage.
2. Unusable file: [20240102.log](postgres/2024/20240102.log) contains only the string "Bad file". It is intended not to be
ingested successfully, but rather to check that the HL7 ingestion process will not entirely fail when
encountering a file like this.
3. File with unparsable report: [20230113.log](postgres/2023/20230113.log) contains HL7-like messages in addition to
a message that matches the encoding rules of individual messages within the log files. The goal is to double check
that attempting to ingest an HL7-ish log file that is generally well-formed but which contains an unparseable message
will _not_ cause the other reports in the same log file to fail the ingestion process.
4. File with message containing duplicate HL7 content: [20071021.log](postgres/2007/20071021.log) contains two identical
HL7 reports with no timestamp header line between them. Both should be ingested successfully.
5. File with no HL7: [20190106.log](postgres/2019/20190106.log) contains a message with no HL7 content, just message tags (<SB><EB><R>).
It should be marked as an error: empty HL7. 
6. File with junk HL7: [20160829.log](postgres/2016/20160829.log) contains a message with garbage in the HL7 content.
It should be marked as an error: unparsable HL7.
7. Multiple log files for the same date: [20000216.log](postgres/2000/20000216.log) and [20000216_PORU.log](postgres/2000/20000216_PORU.log)
share the date 2000-02-16. Both should be ingested successfully, and the HL7 filenames should use the full log filename
as a prefix (`20000216_0.hl7` and `20000216_PORU_0.hl7` respectively) to avoid collisions.
8. Deterministic OBX line order (issue #537): [20260601.log](obx_ordering/20260601.log) holds 150 multi-OBX messages
whose OBX-5 values are zero-padded, strictly ascending markers in file order, laid out in contiguous `&GDT` / `&IMP` /
`&TCM` / `&ADT` blocks (findings / impression / technician_note / addendum). It lives under its own `obx_ordering/` root
and is ingested only by `TestScoutQueries.testObxLineOrderIsDeterministic` (into its own table, so it does not affect the
exact-count assertions of the other corpora). A correctly assembled report is those markers in ascending order; the test
asserts that and that a byte-identical re-ingest reproduces byte-identical `report_text` per `source_file`. This corpus is
synthetic and was generated programmatically rather than by the data-generator. See §"Steps to reproduce" of the issue:
forcing the reorder needs large-scale shuffle/spill pressure, so a single-node CI run is unlikely to reproduce it on its
own — the test is a faithful regression guard, deterministically green with the fix.

The original design of these tests allowed for the capability for the [data-generator](https://github.com/washu-tag/data-generator)
to generate test data with corresponding tests as JSON automatically. However, as we have added more tests, they have required more
test data with odd edge cases that the data-generator should not create. We've moved away from easy regeneration as a requirement,
at least in part because the test data has become more mature where it is less likely that it will be needed often.
Situations we'll need to consider if we pick that design back up include:

1. The test JSON was manually updated to have knowledge of the PORU files manually.
2. Some patient DOBs in the test files were manually tweaked to cause some edge cases in the patient age tests.
