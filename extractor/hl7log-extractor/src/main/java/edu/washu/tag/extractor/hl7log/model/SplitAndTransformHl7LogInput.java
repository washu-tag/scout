package edu.washu.tag.extractor.hl7log.model;

public record SplitAndTransformHl7LogInput(String logPath, String hl7OutputPath, String scratchDir) { }
