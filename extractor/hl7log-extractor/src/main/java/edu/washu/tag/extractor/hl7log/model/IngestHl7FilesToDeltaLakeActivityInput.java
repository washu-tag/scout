package edu.washu.tag.extractor.hl7log.model;

/**
 * Ingest HL7 files to Delta Lake activity input.
 *
 * <p>Input for the base-ingest half of the pipeline (issue #457): parse HL7 and merge
 * into the base report table. Derivative-table options moved to
 * {@link DeriveDeltaTablesActivityInput}.
 *
 * @param reportTableName Name of the report table to be created in Delta Lake.
 * @param hl7ManifestFilePath Path to the HL7 manifest file that contains the list of HL7 files to be ingested.
 */
public record IngestHl7FilesToDeltaLakeActivityInput(
    String reportTableName,
    String hl7ManifestFilePath
) {}
