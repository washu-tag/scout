package edu.washu.tag.temporal.model;

public record ContinueIngestWorkflow(String manifestFilePath, int numLogFiles, int nextIndex) {}
