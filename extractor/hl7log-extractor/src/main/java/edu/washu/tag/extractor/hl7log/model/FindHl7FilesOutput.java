package edu.washu.tag.extractor.hl7log.model;

/**
 * Output for the activity that finds HL7 files.
 *
 * @param hl7ManifestFilePath the path to the generated HL7 manifest file
 */
public record FindHl7FilesOutput(String hl7ManifestFilePath) {}
