package edu.washu.tag.extractor.hl7log.db;

import edu.washu.tag.extractor.hl7log.db.DbUtils.FileStatusStatus;
import edu.washu.tag.extractor.hl7log.db.DbUtils.FileStatusType;

public record FileStatus(
    String filePath,
    String type,
    String status,
    String errorMessage,
    String workflowId,
    String activityId,
) {
    public static FileStatus parsed(String filePath, String workflowId, String activityId) {
        return new FileStatus(filePath, FileStatusType.LOG.getType(), FileStatusStatus.PARSED.getStatus(), null, workflowId, activityId);
    }

    public static FileStatus staged(String filePath, FileStatusType type, String workflowId, String activityId) {
        return new FileStatus(filePath, type.getType(), FileStatusStatus.STAGED.getStatus(), null, workflowId, activityId);
    }

    public static FileStatus failed(String filePath, FileStatusType type, String errorMessage, String workflowId, String activityId) {
        return new FileStatus(filePath, type.getType(), FileStatusStatus.FAILED.getStatus(), errorMessage, workflowId, activityId);
    }

    public boolean wasStaged() {
        return FileStatusStatus.STAGED.getStatus().equals(status);
    }
}
