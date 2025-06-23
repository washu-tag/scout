package edu.washu.tag.extractor.hl7log.activity;

import edu.washu.tag.extractor.hl7log.model.FindHl7FilesInput;
import edu.washu.tag.extractor.hl7log.model.FindHl7FilesOutput;
import edu.washu.tag.extractor.hl7log.model.WriteHl7FilesErrorStatusToDbInput;
import edu.washu.tag.extractor.hl7log.model.WriteHl7FilesErrorStatusToDbOutput;
import io.temporal.activity.ActivityInterface;
import io.temporal.activity.ActivityMethod;

/**
 * Activity to find HL7 files.
 */
@ActivityInterface
public interface FindHl7Files {

    /**
     * Finds HL7 files in the specified root path and writes their paths to a manifest file.
     *
     * @param input The input containing the root path and other parameters.
     * @return The output containing the path to the generated manifest file.
     */
    @ActivityMethod
    FindHl7FilesOutput findHl7FilesAndWriteManifest(FindHl7FilesInput input);

    /**
     * Writes error statuses for HL7 files to the database based on a manifest file.
     *
     * @param input The input containing the manifest file path and error details.
     * @return The output indicating the result of the database operation.
     */
    @ActivityMethod
    WriteHl7FilesErrorStatusToDbOutput writeHl7FilesErrorStatusToDb(WriteHl7FilesErrorStatusToDbInput input);
}
