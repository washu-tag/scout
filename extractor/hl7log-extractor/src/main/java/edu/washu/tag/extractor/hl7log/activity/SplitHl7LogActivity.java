package edu.washu.tag.extractor.hl7log.activity;

import edu.washu.tag.extractor.hl7log.model.Hl7ManifestFileInput;
import edu.washu.tag.extractor.hl7log.model.Hl7ManifestFileOutput;
import edu.washu.tag.extractor.hl7log.model.SplitAndTransformHl7LogInput;
import edu.washu.tag.extractor.hl7log.model.SplitAndTransformHl7LogOutput;
import io.temporal.activity.ActivityInterface;
import io.temporal.activity.ActivityMethod;

/**
 * Activity for splitting and transforming HL7 log files.
 */
@ActivityInterface
public interface SplitHl7LogActivity {

    /**
     * Splits and transforms an HL7 log file into individual HL7 messages.
     *
     * @param input The input containing the log file path and other parameters.
     * @return The output containing the result of the split and transform operation.
     */
    @ActivityMethod
    SplitAndTransformHl7LogOutput splitAndTransformHl7Log(SplitAndTransformHl7LogInput input);

    /**
     * Writes a manifest file containing paths to HL7 files.
     *
     * @param input The input containing the list of HL7 file paths and other parameters.
     * @return The output containing the path to the generated manifest file.
     */
    @ActivityMethod
    Hl7ManifestFileOutput writeHl7ManifestFile(Hl7ManifestFileInput input);
}
