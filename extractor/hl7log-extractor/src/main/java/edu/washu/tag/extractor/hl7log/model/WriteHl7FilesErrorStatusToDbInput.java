package edu.washu.tag.extractor.hl7log.model;

public record WriteHl7FilesErrorStatusToDbInput(String manifestFilePath, String errorMessage) {}
