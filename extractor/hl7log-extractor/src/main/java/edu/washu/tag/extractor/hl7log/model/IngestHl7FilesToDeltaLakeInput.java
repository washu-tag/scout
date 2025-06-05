package edu.washu.tag.extractor.hl7log.model;

/**
 * Ingest HL7 files to Delta Lake activity input.
 *
 * @param modalityMapPath Path to read modality map file, which is the source of the modality column in Delta Lake table.
 * @param scratchSpaceRootPath Root path to use for temporary files.
 * @param hl7ManifestFilePath Path to the HL7 manifest file, which contains the list of HL7 files to be ingested.
 *                            This file will exist if this is being run as part of the IngestHl7LogWorkflow, but may
 *                            not exist if this is being run on its own.
 * @param hl7RootPath Root path where HL7 files are located. This is used to find the HL7 files if
 *                    {@link IngestHl7FilesToDeltaLakeInput#hl7ManifestFilePath} is not provided.
 * @param reportTableName Name of the report table to be created in Delta Lake.
 * @param deltaIngestTimeout Timeout for the Delta Lake ingest operation in minutes.
 */
public record IngestHl7FilesToDeltaLakeInput(
    String modalityMapPath,
    String scratchSpaceRootPath,
    String hl7ManifestFilePath,
    String hl7RootPath,
    String reportTableName,
    Integer deltaIngestTimeout
) {}
