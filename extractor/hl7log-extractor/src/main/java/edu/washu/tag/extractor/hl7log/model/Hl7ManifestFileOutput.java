package edu.washu.tag.extractor.hl7log.model;

/**
 * Output parameters for writing HL7 manifest files.
 *
 * @param manifestFilePath Path to the manifest file.
 * @param numHl7Files Number of HL7 file paths in the manifest file.
 */
public record Hl7ManifestFileOutput(String manifestFilePath, int numHl7Files) {}
