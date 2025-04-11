package edu.washu.tag.temporal.db;

import java.nio.file.Paths;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public record LogFile(
    int logId,
    String filePath,
    LocalDate date,
    String status,
    String errorMessage,
    String workflowId,
    String activityId,
    LocalDateTime processedAt
) {

    private static final Pattern DATE_PATTERN = Pattern.compile("\\d{4}-\\d{2}-\\d{2}");
    private static final DateTimeFormatter DATE_FORMATTER = DateTimeFormatter.ofPattern("yyyy-MM-dd");

    private static LocalDate dateFromFilePath(String filePath) {
        String fileName = Paths.get(filePath).getFileName().toString();
        Matcher m = DATE_PATTERN.matcher(fileName);
        if (m.find()) {
            return LocalDate.parse(m.group(), DATE_FORMATTER);
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
