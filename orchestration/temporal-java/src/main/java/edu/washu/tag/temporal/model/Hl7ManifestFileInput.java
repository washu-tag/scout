package edu.washu.tag.temporal.model;

import java.util.List;

public record Hl7ManifestFileInput(List<String> hl7FilePathFiles, String manifestFileDirPath) {}
