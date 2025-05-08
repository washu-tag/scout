package edu.washu.tag.extractor.hl7log.model;

public record ContinueIngestWorkflow(String manifestFilePath, int numLogFiles, int nextIndex, int splitAndUploadConcurrency) {}
