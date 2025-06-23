package edu.washu.tag.extractor.hl7log.model;

/**
 * Ingest HL7 files to Delta Lake activity input.
 *
 * @param reportTableName Name of the report table to be created in Delta Lake.
 * @param modalityMapPath Path to read modality map file, which is the source of the modality column in Delta Lake table.
 * @param hl7ManifestFilePath Path to the HL7 manifest file that contains the list of HL7 files to be ingested.
 */
public record IngestHl7FilesToDeltaLakeActivityInput(
    String reportTableName,
    String modalityMapPath,
    String hl7ManifestFilePath
) {}
