package edu.washu.tag.extractor.hl7log.model;

import java.util.List;

public record FindHl7LogFileInput(List<String> logPaths, String date, String logsRootPath, String manifestFilePath, int splitAndUploadConcurrency) {}
