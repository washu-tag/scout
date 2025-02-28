package edu.washu.tag.temporal.model;

import java.util.List;

public record WriteHl7FilePathsFileInput(List<String> hl7FilePaths, String scratchSpacePath) { }
