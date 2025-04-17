package edu.washu.tag.temporal.db;

import java.util.List;

public interface IngestDbService {
    void insertLogFile(LogFile logFile);
    void batchInsertHl7Files(List<Hl7File> hl7Files);
}
