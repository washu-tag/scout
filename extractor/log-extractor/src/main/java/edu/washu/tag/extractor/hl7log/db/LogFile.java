package edu.washu.tag.extractor.hl7log.db;

import java.nio.file.Paths;
import java.time.LocalDate;
import java.util.regex.Matcher;

public record LogFile(
    String filePath,
    LocalDate date,
    String status,
    String errorMessage,
    String workflowId,
    String activityId
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
        return new LogFile(filePath, dateFromFilePath(filePath), DbUtils.SUCCEEDED, null, workflowId, activityId);
    }

    public static LogFile error(String filePath, String error, String workflowId, String activityId) {
        return new LogFile(filePath, dateFromFilePath(filePath), DbUtils.FAILED, error, workflowId, activityId);
    }
}
