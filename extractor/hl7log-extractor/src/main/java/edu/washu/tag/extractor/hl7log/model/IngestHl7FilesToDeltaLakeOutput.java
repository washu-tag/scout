package edu.washu.tag.extractor.hl7log.model;

/**
 * Ingest HL7 files to Delta Lake activity output.
 *
 * @param numHl7Ingested Number of HL7 files ingested into Delta Lake.
 */
public record IngestHl7FilesToDeltaLakeOutput(int numHl7Ingested) {}
