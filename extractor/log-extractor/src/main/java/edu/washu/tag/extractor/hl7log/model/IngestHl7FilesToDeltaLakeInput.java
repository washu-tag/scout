package edu.washu.tag.extractor.hl7log.model;

public record IngestHl7FilesToDeltaLakeInput(String deltaTable, String modalityMapPath, String scratchSpaceRootPath, String hl7ManifestFilePath, String hl7RootPath) {}
