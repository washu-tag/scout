package edu.washu.tag.temporal.db;

import java.time.LocalDateTime;

public record Hl7File(
    int hl7Id,
    String logFilePath,
    int segmentNumber,
    String filePath,
    String status,
    String errorMessage,
    String workflowId,
    String activityId,
    LocalDateTime processedAt
) {

    public static Hl7File success(String logFilePath, int segmentNumber, String filePath, String workflowId, String activityId) {
        return new Hl7File(0, logFilePath, segmentNumber, filePath, DbUtils.SUCCEEDED, null, workflowId, activityId, null);
    }

    public static Hl7File error(String logFilePath, int segmentNumber, String filePath, String error, String workflowId, String activityId) {
        return new Hl7File(0, logFilePath, segmentNumber, filePath, DbUtils.FAILED, error, workflowId, activityId, null);
    }
}
