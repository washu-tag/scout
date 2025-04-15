package edu.washu.tag.temporal.db;

import java.nio.file.Paths;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.util.regex.Matcher;

public record LogFile(
    int id,
    String filePath,
    LocalDate date,
    String status,
    String errorMessage,
    String workflowId,
    String activityId,
    LocalDateTime processedAt
) {

    private static LocalDate dateFromFilePath(String filePath) {
        String fileName = Paths.get(filePath).getFileName().toString();
        Matcher m = DbUtils.DATE_PATTERN.matcher(fileName);
        if (m.find()) {
            return LocalDate.parse(m.group(), DbUtils.DATE_FORMATTER);
        }
        return null;
    }

    public static LogFile success(String filePath, String workflowId, String activityId) {
        return new LogFile(0, filePath, dateFromFilePath(filePath), DbUtils.SUCCEEDED, null, workflowId, activityId, null);
    }

    public static LogFile error(String filePath, String error, String workflowId, String activityId) {
        return new LogFile(0, filePath, dateFromFilePath(filePath), DbUtils.FAILED, error, workflowId, activityId, null);
    }
}
