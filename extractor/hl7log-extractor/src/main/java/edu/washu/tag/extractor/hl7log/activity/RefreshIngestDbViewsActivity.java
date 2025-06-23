package edu.washu.tag.extractor.hl7log.activity;

import edu.washu.tag.extractor.hl7log.model.RefreshIngestDbViewsInput;
import edu.washu.tag.extractor.hl7log.model.RefreshIngestDbViewsOutput;
import io.temporal.activity.ActivityInterface;
import io.temporal.activity.ActivityMethod;

/**
 * Activity for refreshing ingest database views.
 */
@ActivityInterface
public interface RefreshIngestDbViewsActivity {

    /**
     * Refreshes the ingest database views.
     *
     * @param input The input containing parameters for the refresh operation.
     * @return The output indicating the result of the refresh operation.
     */
    @ActivityMethod
    RefreshIngestDbViewsOutput refreshIngestDbViews(RefreshIngestDbViewsInput input);
}
