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
4. File with message containing duplicate HL7 content: [20071021.log](postgres/2007/20071021.log) contains a message with
two HL7 reports. We expect to see the first ingested successfully and a complaint about the second because it doesn't
have a header line with a timestamp.
5. File with no HL7: [20190106.log](postgres/2019/20190106.log) contains a message with no HL7 content, just message tags (<SB><EB><R>).
It should be marked as an error: empty HL7. 
6. File with junk HL7: [20160829.log](postgres/2016/20160829.log) contains a message with garbage in the HL7 content.
It should be marked as an error: unparsable HL7.