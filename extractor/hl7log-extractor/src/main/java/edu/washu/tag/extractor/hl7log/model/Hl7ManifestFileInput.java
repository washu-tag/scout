package edu.washu.tag.extractor.hl7log.model;

import java.util.List;

public record Hl7ManifestFileInput(List<String> hl7FilePathFiles, String manifestFileDirPath) {}
