package edu.washu.tag.extractor.hl7log.db;

import java.time.LocalDate;
import java.util.List;

public interface IngestDbService {
    void insertFileStatus(FileStatus fileStatus);
    void batchInsertHl7Files(List<Hl7File> hl7Files);
    void batchInsertFileStatuses(List<FileStatus> fileStatuses);
    void batchInsertNewHl7FileStatuses(List<FileStatus> fileStatuses, String logPath, LocalDate date);
}
