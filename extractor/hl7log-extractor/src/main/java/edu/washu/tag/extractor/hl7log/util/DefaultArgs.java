package edu.washu.tag.extractor.hl7log.util;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

/**
 * DefaultArgs provides default values for workflow arguments used in the HL7 log ingestion workflows.
 * These defaults can be overridden by input values provided to the workflows.
 */
@Component
public class DefaultArgs {
    // Default values for workflow arguments
    // Must be static so we can access them from non-Spring-Component workflow classes
    private static String logsRootPath;
    private static String scratchSpaceRootPath;
    private static String hl7OutputPath;
    private static Integer splitAndUploadTimeout;
    private static Integer splitAndUploadHeartbeatTimeout;
    private static Integer splitAndUploadConcurrency;
    private static String reportTableName;
    private static Integer deltaIngestTimeout;

    /**
     * Logs root path for HL7 log files.
     *
     * @param logsRootPath The input value for logs root path.
     */
    @Value("${scout.workflowArgDefaults.ingestHl7Log.logsRootPath}")
    public void setLogsRootPath(String logsRootPath) {
        DefaultArgs.logsRootPath = logsRootPath;
    }

    /**
     * Logs root path for HL7 log files.
     *
     * @param input The input value for logs root path.
     * @return The logs root path value or the default.
     */
    public static String getLogsRootPath(String input) {
        return getValueOrDefault(input, logsRootPath);
    }

    /**
     * Scratch space root path for temporary files.
     *
     * @param scratchSpaceRootPath The input value for scratch space root path.
     */
    @Value("${scout.workflowArgDefaults.ingestHl7Log.scratchSpaceRootPath}")
    public void setScratchSpaceRootPath(String scratchSpaceRootPath) {
        DefaultArgs.scratchSpaceRootPath = scratchSpaceRootPath;
    }

    /**
     * Scratch space root path for temporary files.
     *
     * @param input The input value for scratch space root path.
     * @return The scratch space root path value or the default.
     */
    public static String getScratchSpaceRootPath(String input) {
        return getValueOrDefault(input, scratchSpaceRootPath);
    }

    /**
     * HL7 output path for processed HL7 files.
     *
     * @param hl7OutputPath The input value for HL7 output path.
     */
    @Value("${scout.workflowArgDefaults.ingestHl7Log.hl7OutputPath}")
    public void setHl7OutputPath(String hl7OutputPath) {
        DefaultArgs.hl7OutputPath = hl7OutputPath;
    }

    /**
     * HL7 output path for processed HL7 files.
     *
     * @param input The input value for HL7 output path.
     * @return The HL7 output path value or the default.
     */
    public static String getHl7OutputPath(String input) {
        return getValueOrDefault(input, hl7OutputPath);
    }

    /**
     * Split and upload activity start-to-close timeout.
     *
     * @param splitAndUploadTimeout The input value for split and upload timeout.
     */
    @Value("${scout.workflowArgDefaults.ingestHl7Log.splitAndUploadTimeout}")
    public void setSplitAndUploadTimeout(Integer splitAndUploadTimeout) {
        DefaultArgs.splitAndUploadTimeout = splitAndUploadTimeout;
    }

    /**
     * Split and upload activity start-to-close timeout.
     *
     * @param input The input value for split and upload timeout.
     */
    public static Integer getSplitAndUploadTimeout(Integer input) {
        return getValueOrDefault(input, splitAndUploadTimeout);
    }

    /**
     * Split and upload activity heartbeat timeout.
     *
     * @param splitAndUploadHeartbeatTimeout The input value for split and upload heartbeat timeout.
     */
    @Value("${scout.workflowArgDefaults.ingestHl7Log.splitAndUploadHeartbeatTimeout}")
    public void setSplitAndUploadHeartbeatTimeout(Integer splitAndUploadHeartbeatTimeout) {
        DefaultArgs.splitAndUploadHeartbeatTimeout = splitAndUploadHeartbeatTimeout;
    }

    /**
     * Split and upload activity heartbeat timeout.
     *
     * @param input The input value for split and upload heartbeat timeout.
     */
    public static Integer getSplitAndUploadHeartbeatTimeout(Integer input) {
        return getValueOrDefault(input, splitAndUploadHeartbeatTimeout);
    }

    /**
     * Split and upload concurrency.
     * This is the number of concurrent activities (i.e. log files) that can be processed by a single
     * ingest HL7 log workflow instance before it continues as new.
     *
     * @param splitAndUploadConcurrency The input value for split and upload concurrency.
     */
    @Value("${scout.workflowArgDefaults.ingestHl7Log.splitAndUploadConcurrency}")
    public void setSplitAndUploadConcurrency(Integer splitAndUploadConcurrency) {
        DefaultArgs.splitAndUploadConcurrency = splitAndUploadConcurrency;
    }

    /**
     * Split and upload concurrency.
     *
     * @param input The input value for split and upload concurrency.
     * @return The split and upload concurrency value or the default.
     */
    public static Integer getSplitAndUploadConcurrency(Integer input) {
        return getValueOrDefault(input, splitAndUploadConcurrency);
    }

    /**
     * Report table name.
     *
     * @param reportTableName The input value for report table name.
     */
    @Value("${scout.workflowArgDefaults.ingestHl7ToDeltaLake.reportTableName}")
    public void setReportTableName(String reportTableName) {
        DefaultArgs.reportTableName = reportTableName;
    }

    /**
     * Report table name.
     *
     * @param input The input value for report table name.
     * @return The report table name value or the default.
     */
    public static String getReportTableName(String input) {
        return getValueOrDefault(input, reportTableName);
    }

    /**
     * Delta ingest timeout.
     *
     * @param deltaIngestTimeout The input value for delta ingest timeout.
     */
    @Value("${scout.workflowArgDefaults.ingestHl7ToDeltaLake.deltaIngestTimeout}")
    public void setDeltaIngestTimeout(Integer deltaIngestTimeout) {
        DefaultArgs.deltaIngestTimeout = deltaIngestTimeout;
    }

    /**
     * Delta ingest timeout.
     *
     * @param input The input value for delta ingest timeout.
     * @return The delta ingest timeout value or the default.
     */
    public static Integer getDeltaIngestTimeout(Integer input) {
        return getValueOrDefault(input, deltaIngestTimeout);
    }

    private static String getValueOrDefault(String value, String defaultValue) {
        return (value == null || value.isEmpty()) ? defaultValue : value;
    }

    private static Integer getValueOrDefault(Integer value, Integer defaultValue) {
        return (value == null || value == -1) ? defaultValue : value;
    }
}
