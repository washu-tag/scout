package edu.washu.tag.extractor.hl7log.db;

import java.time.LocalDate;
import java.util.List;
import org.springframework.retry.annotation.Backoff;
import org.springframework.retry.annotation.Retryable;

/**
 * Service methods to insert file statuses and HL7 files with retry capabilities.
 */
public interface IngestDbService {
    /**
     * Inserts a single FileStatus record into the database.
     *
     * @param fileStatus the FileStatus record to insert
     */
    @Retryable(maxAttempts = 5, backoff = @Backoff(delay = 5000, multiplier = 2))
    void insertFileStatus(FileStatus fileStatus);

    /**
     * Inserts a batch of HL7File records into the database.
     *
     * @param hl7Files the list of HL7File records to insert
     */
    @Retryable(maxAttempts = 5, backoff = @Backoff(delay = 5000, multiplier = 2))
    void batchInsertHl7Files(List<Hl7File> hl7Files);

    /**
     * Inserts a batch of FileStatus records into the database.
     *
     * @param fileStatuses the list of FileStatus records to insert
     */
    @Retryable(maxAttempts = 5, backoff = @Backoff(delay = 5000, multiplier = 2))
    void batchInsertFileStatuses(List<FileStatus> fileStatuses);

    /**
     * Inserts a batch of FileStatus records for new HL7 files.
     *
     * <p>This method is used to insert records for HL7 files that have been staged
     * and which may not yet have been recorded in the hl7_file table.
     *
     * @param fileStatuses the list of FileStatus records to insert
     * @param logPath      the path to the log file associated with the HL7 files
     * @param date         the date associated with the HL7 files
     */
    @Retryable(maxAttempts = 5, backoff = @Backoff(delay = 5000, multiplier = 2))
    void batchInsertNewHl7FileStatuses(List<FileStatus> fileStatuses, String logPath, LocalDate date);
}
