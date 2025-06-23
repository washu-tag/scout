package edu.washu.tag.extractor.hl7log.model;

/**
 * Input parameters for continuing-as-new an HL7 log ingestion workflow.
 *
 * @param manifestFilePath The path to the manifest file containing log file information.
 * @param numLogFiles The total number of log files to process.
 * @param nextIndex The index of the next log file to process.
 * @param splitAndUploadConcurrency The concurrency level for splitting and uploading log files.
 */
public record ContinueIngestWorkflow(String manifestFilePath, int numLogFiles, int nextIndex, int splitAndUploadConcurrency) {}
