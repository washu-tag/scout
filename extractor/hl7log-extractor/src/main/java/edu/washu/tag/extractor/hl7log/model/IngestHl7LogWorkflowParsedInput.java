package edu.washu.tag.extractor.hl7log.model;

import java.util.List;

public record IngestHl7LogWorkflowParsedInput(
    List<String> logPaths,
    String date,
    String scratchSpaceRootPath,
    String logsRootPath,
    String hl7OutputPath,
    Integer splitAndUploadTimeout,
    Integer splitAndUploadConcurrency
) {}
