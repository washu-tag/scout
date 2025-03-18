package edu.washu.tag.temporal.model;

public record IngestHl7FilesToDeltaLakeInput(String deltaTable, String modalityMapPath, String hl7ManifestFilePath) {}
