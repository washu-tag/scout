package edu.washu.tag.extractor.hl7log.activity;

import io.temporal.activity.ActivityInterface;
import io.temporal.activity.ActivityMethod;

/**
 * Activity to signal the view refresh entity workflow.
 * Uses signalWithStart to atomically start the workflow if not running, or signal it if already running.
 */
@ActivityInterface
public interface SignalRefreshActivity {
    /**
     * Signal the view refresh entity workflow to request a refresh.
     * If the workflow is not running, it will be started first.
     *
     * @param sourceWorkflowId The ID of the workflow requesting the refresh.
     */
    @ActivityMethod
    void signalRefresh(String sourceWorkflowId);
}
