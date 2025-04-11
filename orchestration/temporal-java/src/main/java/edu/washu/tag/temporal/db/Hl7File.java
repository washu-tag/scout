package edu.washu.tag.temporal.db;

import java.time.LocalDateTime;

public record Hl7File(
    int hl7Id,
    String logFilePath,
    int segmentNumber,
    String filePath,
    String status,
    String errorMessage,
    LocalDateTime processedAt
) {

    public static Hl7File success(String logFilePath, int segmentNumber, String filePath) {
        return new Hl7File(0, logFilePath, segmentNumber, filePath, DbUtils.SUCCEEDED, null, null);
    }

    public static Hl7File error(String logFilePath, int segmentNumber, String filePath, String error) {
        return new Hl7File(0, logFilePath, segmentNumber, filePath, DbUtils.FAILED, error, null);
    }
}
