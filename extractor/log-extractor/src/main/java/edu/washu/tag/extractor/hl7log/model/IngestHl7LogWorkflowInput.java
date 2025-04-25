package edu.washu.tag.extractor.hl7log.model;

/**
 * Input for the IngestHl7LogWorkflow.
 *
 * @param date Find a specific log file within the logsRootPath by date. Must also provide logsRootPath.
 * @param logsRootPath Root path to search recursively for log files. Optional if specific logPaths are provided.
 * @param logPaths List of specific log files to ingest. Can be absolute or, if logsRootPath is provided, relative to logsRootPath.
 *                 If logsRootPath is provided and this is not, every .log file under logsRootPath will be used.
 * @param scratchSpaceRootPath Root path to use for temporary files. Will be created if it does not exist.
 * @param hl7OutputPath Path to write HL7 files.
 * @param deltaLakePath Path to write Delta Lake files.
 * @param modalityMapPath Path to read modality map file, which is the source of the modality column in Delta Lake table.
 * @param continued Do not set this on initial workflow run. Parameters needed to resume when workflow is Continued As New.
 */
public record IngestHl7LogWorkflowInput(
        String date,
        String logsRootPath,
        String logPaths,
        String scratchSpaceRootPath,
        String hl7OutputPath,
        String deltaLakePath,
        String modalityMapPath,
        ContinueIngestWorkflow continued
) {}
