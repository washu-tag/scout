package edu.washu.tag.extractor.hl7log.activity;

import edu.washu.tag.extractor.hl7log.model.RefreshIngestDbViewsInput;
import edu.washu.tag.extractor.hl7log.model.RefreshIngestDbViewsOutput;
import io.temporal.activity.ActivityInterface;
import io.temporal.activity.ActivityMethod;

@ActivityInterface
public interface RefreshIngestDbViewsActivity {
    @ActivityMethod
    RefreshIngestDbViewsOutput refreshIngestDbViews(RefreshIngestDbViewsInput input);
}
