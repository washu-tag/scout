package edu.washu.tag.temporal.model;

import java.util.List;

public record IngestHl7FilesToDeltaLakeInput(String deltaTable, String modalityMapPath, List<String> hl7FilePathFiles) { }
