package edu.washu.tag.temporal.model;

public record IngestHl7LogWorkflowInput(String logPath, String scratchSpaceRootPath, String outputRootPath) { }
