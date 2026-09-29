package edu.washu.tag.extractor.hl7log.model;

import java.util.List;

/**
 * Parsed input for the IngestHl7LogWorkflow.
 *
 * @param logPaths Paths to log files to process.
 * @param date Date string. Log files will be filtered by this date.
 * @param scratchSpaceRootPath Root path to use for temporary files. Will be created if it does not exist.
 * @param logsRootPath Root path to search recursively for log files. Optional if specific logPaths are provided.
 * @param hl7OutputPath Path to write HL7 files.
 * @param splitAndUploadTimeout The timeout for the split and upload job in minutes.
 * @param splitAndUploadHeartbeatTimeout The heartbeat timeout for the split and upload job in minutes.
 * @param splitAndUploadConcurrency How many logs we should process before continuing as new.
 */
public record IngestHl7LogWorkflowParsedInput(
    List<String> logPaths,
    String date,
    String scratchSpaceRootPath,
    String logsRootPath,
    String hl7OutputPath,
    Integer splitAndUploadTimeout,
    Integer splitAndUploadHeartbeatTimeout,
    Integer splitAndUploadConcurrency
) {}
