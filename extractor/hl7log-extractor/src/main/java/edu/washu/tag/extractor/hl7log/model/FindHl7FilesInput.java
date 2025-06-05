package edu.washu.tag.extractor.hl7log.model;

/**
 * Input for the activity that finds HL7 files.
 *
 * @param hl7RootPath Root path to search recursively for HL7 files.
 * @param scratchDir Directory to use for temporary files.
 */
public record FindHl7FilesInput(String hl7RootPath, String scratchDir) {}
