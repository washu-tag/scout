package edu.washu.tag.extractor.hl7log.activity;

import io.temporal.activity.ActivityInterface;
import io.temporal.activity.ActivityMethod;

/**
 * Activity to check if any HL7 transformer workflows are currently running.
 */
@ActivityInterface
public interface CheckRunningTransformersActivity {
    /**
     * Check if any IngestHl7ToDeltaLakeWorkflow instances are currently running.
     *
     * @return true if at least one transformer workflow is running, false otherwise.
     */
    @ActivityMethod
    boolean areTransformersRunning();
}
