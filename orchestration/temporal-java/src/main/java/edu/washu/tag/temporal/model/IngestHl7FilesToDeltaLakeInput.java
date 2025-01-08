package edu.washu.tag.temporal.model;

import java.util.List;

public record IngestHl7FilesToDeltaLakeInput(String outputRootPath, List<String> hl7RelativePaths) { }
