package edu.washu.tag.extractor.hl7log.model;

import java.util.List;

/**
 * Input for the activity that finds HL7 log files.
 *
 * @param logPaths List of log files to process.
 * @param date Date string. Log files will be filtered by this date.
 * @param logsRootPath Root path where the log files are located.
 * @param manifestFilePath Path to the manifest file where results will be written.
 * @param splitAndUploadConcurrency Number of log files to process concurrently during split and upload operations.
 *                                  This is used to determine the next index to process in the manifest file.
 */
public record FindHl7LogFileInput(List<String> logPaths, String date, String logsRootPath, String manifestFilePath, int splitAndUploadConcurrency) {}
