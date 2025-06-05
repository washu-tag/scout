package edu.washu.tag.extractor.hl7log.model;

/**
 * Output for the SplitAndTransformHl7Log activity.
 *
 * @param hl7FilesOutputFilePath Path to the output file containing HL7 files
 * @param numHl7Files Number of HL7 files generated
 */
public record SplitAndTransformHl7LogOutput(String hl7FilesOutputFilePath, int numHl7Files) {}
