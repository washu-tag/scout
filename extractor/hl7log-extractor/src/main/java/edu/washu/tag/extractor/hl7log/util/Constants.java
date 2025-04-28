package edu.washu.tag.extractor.hl7log.util;

public class Constants {
    public static final String PARENT_QUEUE = "ingest-hl7-log";
    public static final String CHILD_QUEUE = "split-transform-hl7-log";
    public static final String PYTHON_ACTIVITY = "ingest_hl7_files_to_delta_lake";
    public static final String BUILD_MANIFEST_QUEUE = "build-manifest";
    public static final String INGEST_DELTA_LAKE_QUEUE = "ingest-hl7-delta-lake";
}
