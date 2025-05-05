package edu.washu.tag.extractor.hl7log.model;

public record IngestHl7FilesToDeltaLakeActivityInput(
    String reportTableName,
    String modalityMapPath,
    String hl7ManifestFilePath
) {}
