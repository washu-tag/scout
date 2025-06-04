package edu.washu.tag.extractor.hl7log.util;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

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
    private static String modalityMapPath;
    private static String reportTableName;
    private static Integer deltaIngestTimeout;

    @Value("${scout.workflowArgDefaults.ingestHl7Log.logsRootPath}")
    public void setLogsRootPath(String logsRootPath) {
        DefaultArgs.logsRootPath = logsRootPath;
    }

    public static String getLogsRootPath(String input) {
        return getValueOrDefault(input, logsRootPath);
    }

    @Value("${scout.workflowArgDefaults.ingestHl7Log.scratchSpaceRootPath}")
    public void setScratchSpaceRootPath(String scratchSpaceRootPath) {
        DefaultArgs.scratchSpaceRootPath = scratchSpaceRootPath;
    }

    public static String getScratchSpaceRootPath(String input) {
        return getValueOrDefault(input, scratchSpaceRootPath);
    }

    @Value("${scout.workflowArgDefaults.ingestHl7Log.hl7OutputPath}")
    public void setHl7OutputPath(String hl7OutputPath) {
        DefaultArgs.hl7OutputPath = hl7OutputPath;
    }

    public static String getHl7OutputPath(String input) {
        return getValueOrDefault(input, hl7OutputPath);
    }

    @Value("${scout.workflowArgDefaults.ingestHl7Log.splitAndUploadTimeout}")
    public void setSplitAndUploadTimeout(Integer splitAndUploadTimeout) {
        DefaultArgs.splitAndUploadTimeout = splitAndUploadTimeout;
    }

    public static Integer getSplitAndUploadTimeout(Integer input) {
        return getValueOrDefault(input, splitAndUploadTimeout);
    }

    @Value("${scout.workflowArgDefaults.ingestHl7Log.splitAndUploadHeartbeatTimeout}")
    public void setSplitAndUploadHeartbeatTimeout(Integer splitAndUploadHeartbeatTimeout) {
        DefaultArgs.splitAndUploadHeartbeatTimeout = splitAndUploadHeartbeatTimeout;
    }

    public static Integer getSplitAndUploadHeartbeatTimeout(Integer input) {
        return getValueOrDefault(input, splitAndUploadHeartbeatTimeout);
    }

    @Value("${scout.workflowArgDefaults.ingestHl7Log.splitAndUploadConcurrency}")
    public void setSplitAndUploadConcurrency(Integer splitAndUploadConcurrency) {
        DefaultArgs.splitAndUploadConcurrency = splitAndUploadConcurrency;
    }

    public static Integer getSplitAndUploadConcurrency(Integer input) {
        return getValueOrDefault(input, splitAndUploadConcurrency);
    }

    @Value("${scout.workflowArgDefaults.ingestHl7ToDeltaLake.modalityMapPath}")
    public void setModalityMapPath(String modalityMapPath) {
        DefaultArgs.modalityMapPath = modalityMapPath;
    }

    public static String getModalityMapPath(String input) {
        return getValueOrDefault(input, modalityMapPath);
    }

    @Value("${scout.workflowArgDefaults.ingestHl7ToDeltaLake.reportTableName}")
    public void setReportTableName(String reportTableName) {
        DefaultArgs.reportTableName = reportTableName;
    }

    public static String getReportTableName(String input) {
        return getValueOrDefault(input, reportTableName);
    }

    @Value("${scout.workflowArgDefaults.ingestHl7ToDeltaLake.deltaIngestTimeout}")
    public void setDeltaIngestTimeout(Integer deltaIngestTimeout) {
        DefaultArgs.deltaIngestTimeout = deltaIngestTimeout;
    }

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
