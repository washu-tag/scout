package edu.washu.tag.extractor.hl7log.db;

import edu.washu.tag.extractor.hl7log.db.DbUtils.FileStatusStatus;
import edu.washu.tag.extractor.hl7log.db.DbUtils.FileStatusType;

/**
 * Represents a row in the "file_status" table of the ingest database.
 */
public record FileStatus(
    String filePath,
    String type,
    String status,
    String errorMessage,
    String workflowId,
    String activityId
) {

    /**
     * Creates a FileStatus instance for a parsed log file.
     *
     * @param filePath the path to the log file
     * @param workflowId the ID of the workflow in which the log file was processed
     * @param activityId the ID of the activity in which the log file was processed
     * @return a FileStatus instance representing the parsed log file
     */
    public static FileStatus parsed(String filePath, String workflowId, String activityId) {
        return new FileStatus(filePath, FileStatusType.LOG.getType(), FileStatusStatus.PARSED.getStatus(), null, workflowId, activityId);
    }

    /**
     * Creates a FileStatus instance for a staged HL7 file.
     *
     * @param filePath   the path to the HL7 file
     * @param workflowId the ID of the workflow in which the HL7 file was processed
     * @param activityId the ID of the activity in which the HL7 file was processed
     * @return a FileStatus instance representing the staged HL7 file
     */
    public static FileStatus staged(String filePath, String workflowId, String activityId) {
        return new FileStatus(filePath, FileStatusType.HL7.getType(), FileStatusStatus.STAGED.getStatus(), null, workflowId, activityId);
    }

    /**
     * Creates a FileStatus instance for a failed HL7 or log file.
     *
     * @param filePath      the path to the file
     * @param errorMessage  the error message associated with the failure
     * @param workflowId    the ID of the workflow in which the file was processed
     * @param activityId    the ID of the activity in which the file was processed
     * @return a FileStatus instance representing the failed file
     */
    public static FileStatus failed(String filePath, FileStatusType type, String errorMessage, String workflowId, String activityId) {
        return new FileStatus(filePath, type.getType(), FileStatusStatus.FAILED.getStatus(), errorMessage, workflowId, activityId);
    }

    /**
     * Checks if the file status is for a staged file.
     *
     * @return true if the file status is {@link FileStatusStatus#STAGED}, false otherwise
     */
    public boolean wasStaged() {
        return FileStatusStatus.STAGED.getStatus().equals(status);
    }
}
