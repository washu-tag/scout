package edu.washu.tag.temporal.model;

public record IngestHl7FilesToDeltaLakeActivityInput(String deltaTable, String modalityMapPath, String hl7ManifestFilePath) {}
