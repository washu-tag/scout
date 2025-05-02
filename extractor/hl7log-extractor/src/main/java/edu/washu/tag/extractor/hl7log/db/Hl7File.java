package edu.washu.tag.extractor.hl7log.db;

public record Hl7File(
    String logFilePath,
    int segmentNumber,
    String filePath,
    String status,
    String errorMessage,
    String workflowId,
    String activityId
) {

    public static Hl7File success(String logFilePath, int segmentNumber, String filePath, String workflowId, String activityId) {
        return new Hl7File(logFilePath, segmentNumber, filePath, DbUtils.SUCCEEDED, null, workflowId, activityId);
    }

    public static Hl7File error(String logFilePath, int segmentNumber, String error, String workflowId, String activityId) {
        return new Hl7File(logFilePath, segmentNumber, null, DbUtils.FAILED, error, workflowId, activityId);
    }

    public static Hl7File error(String logFilePath, int segmentNumber, String hl7FilePath, String error, String workflowId, String activityId) {
        return new Hl7File(logFilePath, segmentNumber, hl7FilePath, DbUtils.FAILED, error, workflowId, activityId);
    }

    public boolean isSuccess() {
        return DbUtils.SUCCEEDED.equals(status);
    }
}
