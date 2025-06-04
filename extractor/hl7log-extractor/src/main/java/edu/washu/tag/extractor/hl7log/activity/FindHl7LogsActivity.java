package edu.washu.tag.extractor.hl7log.activity;

import edu.washu.tag.extractor.hl7log.model.ContinueIngestWorkflow;
import edu.washu.tag.extractor.hl7log.model.FindHl7LogFileInput;
import edu.washu.tag.extractor.hl7log.model.FindHl7LogFileOutput;
import io.temporal.activity.ActivityInterface;
import io.temporal.activity.ActivityMethod;

/**
 * Activity for finding HL7 log files.
 */
@ActivityInterface
public interface FindHl7LogsActivity {

    /**
     * Finds HL7 log files based on the specified input parameters.
     *
     * @param input The input containing paths and filtering criteria.
     * @return The output containing the list of found log files.
     */
    @ActivityMethod
    FindHl7LogFileOutput findHl7LogFiles(FindHl7LogFileInput input);

    /**
     * Continues the ingest workflow with the next batch of HL7 log files.
     *
     * @param input The input containing workflow continuation details.
     * @return The output containing the next batch of log files to process.
     */
    @ActivityMethod
    FindHl7LogFileOutput continueIngestHl7LogWorkflow(ContinueIngestWorkflow input);
}
