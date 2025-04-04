# Test Data Overview

The data in this directory has been generated to check the functionality of the scout platform.
The tests assume that scout has ingested exactly the HL7-ish log files in this directory. Cases to
point out in particular are:

1. Odd report text: [20101010.log](hl7/2010/20101010.log) contains a report with some special characters
added (`±60%÷`) to check that the platform is still extracting the report text correctly. There was a bug
in development for non-ASCII but ISO-8859-1 characters, so that log file has been intentionally encoded
as ISO-8859-1 for regression test coverage.
2. Unusable file: [20240102.log](hl7/2024/20240102.log) contains only the string "Bad file". It is intended not to be
ingested successfully, but rather to check that the HL7 ingestion process will not entirely fail when
encountering a file like this.