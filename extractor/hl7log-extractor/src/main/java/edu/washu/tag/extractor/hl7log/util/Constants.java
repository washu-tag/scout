package edu.washu.tag.extractor.hl7log.util;

import io.temporal.common.SearchAttributeKey;
import java.time.OffsetDateTime;
import java.time.format.DateTimeFormatter;

public class Constants {
    public static final String PARENT_QUEUE = "ingest-hl7-log";
    public static final String CHILD_QUEUE = "split-transform-hl7-log";
    public static final String PYTHON_ACTIVITY = "ingest_hl7_files_to_delta_lake";
    public static final String BUILD_MANIFEST_QUEUE = "build-manifest";
    public static final String INGEST_DELTA_LAKE_QUEUE = "ingest-hl7-delta-lake";
    public static final String REFRESH_VIEWS_QUEUE = "refresh-ingest-db-views";
    public static final String TEMPORAL_SCHEDULED_START_TIME = "TemporalScheduledStartTime";
    public static final SearchAttributeKey<OffsetDateTime> SCHEDULED_START_TIME_SEARCH_ATTRIBUTE_KEY =
        SearchAttributeKey.forOffsetDateTime(Constants.TEMPORAL_SCHEDULED_START_TIME);
    public static final DateTimeFormatter YYYYMMDD_FORMAT = DateTimeFormatter.ofPattern("yyyyMMdd");
    public static final String REFRESH_VIEWS_PROCEDURE_NAME = "refresh_materialized_views";
    public static final int REFRESH_VIEWS_HEARTBEAT_INTERVAL_SECONDS = 5;
    public static final int REFRESH_VIEWS_TIMEOUT_MINUTES = 12 * 60; // 12 hours in minutes
}
