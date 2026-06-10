package edu.washu.tag.extractor.hl7log.model;

/**
 * Ingest HL7 files to Delta Lake activity input.
 *
 * @param reportTableName Name of the report table to be created in Delta Lake.
 * @param hl7ManifestFilePath Path to the HL7 manifest file that contains the list of HL7 files to be ingested.
 * @param createMapping Whether to derive the report-patient mapping table and the epic views that depend on it.
 */
public record IngestHl7FilesToDeltaLakeActivityInput(
    String reportTableName,
    String hl7ManifestFilePath,
    Boolean createMapping
) {}
