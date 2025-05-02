package edu.washu.tag.extractor.hl7log.db;

public record Hl7FileLogFileSegmentNumber(
    String hl7FilePath,
    String logFilePath,
    int segmentNumber
) {}
