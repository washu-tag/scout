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
    private static String deltaLakePath;
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

    @Value("${scout.workflowArgDefaults.ingestHl7ToDeltaLake.deltaLakePath}")
    public void setDeltaLakePath(String deltaLakePath) {
        DefaultArgs.deltaLakePath = deltaLakePath;
    }
    public static String getDeltaLakePath(String input) {
        return getValueOrDefault(input, deltaLakePath);
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
}
