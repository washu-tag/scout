package edu.washu.tag.extractor.hl7log.db;

import java.time.LocalDate;
import java.util.List;
import org.springframework.retry.annotation.Backoff;
import org.springframework.retry.annotation.Retryable;

public interface IngestDbService {
    @Retryable(maxAttempts = 5, backoff = @Backoff(delay = 5000, multiplier = 2))
    void insertFileStatus(FileStatus fileStatus);
    @Retryable(maxAttempts = 5, backoff = @Backoff(delay = 5000, multiplier = 2))
    void batchInsertHl7Files(List<Hl7File> hl7Files);
    @Retryable(maxAttempts = 5, backoff = @Backoff(delay = 5000, multiplier = 2))
    void batchInsertFileStatuses(List<FileStatus> fileStatuses);
    @Retryable(maxAttempts = 5, backoff = @Backoff(delay = 5000, multiplier = 2))
    void batchInsertNewHl7FileStatuses(List<FileStatus> fileStatuses, String logPath, LocalDate date);
}
