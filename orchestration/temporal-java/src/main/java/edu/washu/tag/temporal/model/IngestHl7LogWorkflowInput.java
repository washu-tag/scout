package edu.washu.tag.temporal.model;

public record IngestHl7LogWorkflowInput(
        String date,
        String logsRootPath,
        String logPaths,
        String scratchSpaceRootPath,
        String hl7OutputPath,
        String deltaLakePath,
        String modalityMapPath
) {}
