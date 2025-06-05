package edu.washu.tag.extractor.hl7log.model;

/**
 * Input for the split and transform HL7 log activity.
 *
 * @param logPath Path to the HL7 log file to be processed.
 * @param hl7OutputPath Path where the transformed HL7 files will be stored.
 * @param scratchDir Temporary directory for intermediate processing.
 */
public record SplitAndTransformHl7LogInput(String logPath, String hl7OutputPath, String scratchDir) {}
