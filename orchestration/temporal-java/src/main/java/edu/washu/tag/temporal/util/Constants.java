package edu.washu.tag.temporal.util;

public class Constants {
    public static final String PARENT_QUEUE = "ingest-hl7-log";
    public static final String CHILD_QUEUE = "split-transform-hl7-log";
    public static final String PYTHON_ACTIVITY = "ingest_hl7_files_to_delta_lake_activity";
    public static final String PYTHON_QUEUE = "ingest-hl7-delta-lake";

    public static final int RETURN_LOG_LIST_SIZE_LIMIT = 2000;
}
