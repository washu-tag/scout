package edu.washu.tag.extractor.hl7log.model;

import java.util.List;

public record WriteHl7FilePathsFileInput(List<String> hl7FilePaths, String scratchSpacePath) {}
