package edu.washu.tag.extractor.hl7log.model;

/**
 * Input for the activity that writes the error status of HL7 files to the database.
 *
 * @param manifestFilePath Path to the manifest file containing HL7 files.
 * @param errorMessage Error message to be logged in the database.
 */
public record WriteHl7FilesErrorStatusToDbInput(String manifestFilePath, String errorMessage) {}
