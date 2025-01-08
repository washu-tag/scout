package edu.washu.tag.temporal.activity;

import edu.washu.tag.temporal.model.SplitHl7LogActivityInput;
import edu.washu.tag.temporal.model.SplitHl7LogActivityOutput;
import edu.washu.tag.temporal.model.TransformSplitHl7LogInput;
import edu.washu.tag.temporal.model.TransformSplitHl7LogOutput;
import io.temporal.activity.ActivityInterface;
import io.temporal.activity.ActivityMethod;

@ActivityInterface
public interface IngestHl7LogActivity {
    @ActivityMethod
    SplitHl7LogActivityOutput splitHl7Log(SplitHl7LogActivityInput input);

    @ActivityMethod
    TransformSplitHl7LogOutput transformSplitHl7Log(TransformSplitHl7LogInput input);
}
