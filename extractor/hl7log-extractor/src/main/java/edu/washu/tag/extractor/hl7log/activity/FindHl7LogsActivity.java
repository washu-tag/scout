package edu.washu.tag.extractor.hl7log.activity;

import edu.washu.tag.extractor.hl7log.model.ContinueIngestWorkflow;
import edu.washu.tag.extractor.hl7log.model.FindHl7LogFileInput;
import edu.washu.tag.extractor.hl7log.model.FindHl7LogFileOutput;
import io.temporal.activity.ActivityInterface;
import io.temporal.activity.ActivityMethod;

@ActivityInterface
public interface FindHl7LogsActivity {
    @ActivityMethod
    FindHl7LogFileOutput findHl7LogFiles(FindHl7LogFileInput input);

    @ActivityMethod
    FindHl7LogFileOutput continueIngestHl7LogWorkflow(ContinueIngestWorkflow input);
}
