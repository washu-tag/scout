package edu.washu.tag.temporal.model;

public record IngestHl7FilesToDeltaLakeInput(String deltaTable, String modalityMapPath, String scratchSpaceRootPath, String hl7ManifestFilePath, String hl7RootPath) {}
