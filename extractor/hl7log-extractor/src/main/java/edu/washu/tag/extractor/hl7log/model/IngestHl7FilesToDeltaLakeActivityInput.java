package edu.washu.tag.extractor.hl7log.model;

public record IngestHl7FilesToDeltaLakeActivityInput(String deltaTable, String modalityMapPath, String hl7ManifestFilePath) {}
