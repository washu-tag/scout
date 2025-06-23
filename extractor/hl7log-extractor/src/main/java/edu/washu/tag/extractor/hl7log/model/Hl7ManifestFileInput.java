package edu.washu.tag.extractor.hl7log.model;

import java.util.List;

/**
 * Input parameters for writing HL7 manifest files.
 *
 * @param hl7FilePathFiles HL7 file path files (each of which contain lists of file paths) to combine into the manifest.
 * @param manifestFileDirPath Directory path where the manifest file will be written.
 */
public record Hl7ManifestFileInput(List<String> hl7FilePathFiles, String manifestFileDirPath) {}
