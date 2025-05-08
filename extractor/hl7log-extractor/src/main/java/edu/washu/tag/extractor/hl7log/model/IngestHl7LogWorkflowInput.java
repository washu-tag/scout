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
 * @param splitAndUploadTimeout The timeout for the split and upload job
 * @param splitAndUploadConcurrency The concurrency for the split and upload job (how many logs should we process concurrently?)
 * @param modalityMapPath Path to read modality map file, which is the source of the modality column in Delta Lake table.
 * @param reportTableName Name of the Delta Lake table to write to.
 * @param continued Do not set this on initial workflow run. Parameters needed to resume when workflow is Continued As New.
 */
public record IngestHl7LogWorkflowInput(
        String date,
        String logsRootPath,
        String logPaths,
        String scratchSpaceRootPath,
        String hl7OutputPath,
        int splitAndUploadTimeout,
        int splitAndUploadConcurrency,
        String modalityMapPath,
        String reportTableName,
        ContinueIngestWorkflow continued
) {
    public static IngestHl7LogWorkflowInput EMPTY = new IngestHl7LogWorkflowInput(
            null,
            null,
            null,
            null,
            null,
            -1,
            -1,
            null,
            null,
            null
    );
}
