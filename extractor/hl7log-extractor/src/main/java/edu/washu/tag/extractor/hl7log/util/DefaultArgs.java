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
    private static int splitAndUploadTimeout;
    private static int splitAndUploadConcurrency;
    private static String modalityMapPath;
    private static String reportTableName;

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
    public static void setSplitAndUploadTimeout(int splitAndUploadTimeout) {
        DefaultArgs.splitAndUploadTimeout = splitAndUploadTimeout;
    }
    public static int getSplitAndUploadTimeout(int input) {
        return getValueOrDefault(input, splitAndUploadTimeout);
    }

    @Value("${scout.workflowArgDefaults.ingestHl7Log.splitAndUploadConcurrency}")
    public static void setSplitAndUploadConcurrency(int splitAndUploadConcurrency) {
        DefaultArgs.splitAndUploadConcurrency = splitAndUploadConcurrency;
    }
    public static int getSplitAndUploadConcurrency(int input) {
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

    private static String getValueOrDefault(String value, String defaultValue) {
        return (value == null || value.isEmpty()) ? defaultValue : value;
    }
    private static int getValueOrDefault(int value, int defaultValue) {
        return (value == -1) ? defaultValue : value;
    }
}
