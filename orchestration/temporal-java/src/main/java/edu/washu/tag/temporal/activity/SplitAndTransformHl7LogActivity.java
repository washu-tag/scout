package edu.washu.tag.temporal.activity;

import edu.washu.tag.temporal.model.SplitAndTransformHl7LogInput;
import edu.washu.tag.temporal.model.SplitAndTransformHl7LogOutput;
import edu.washu.tag.temporal.model.WriteHl7FilePathsFileInput;
import edu.washu.tag.temporal.model.WriteHl7FilePathsFileOutput;
import io.temporal.activity.ActivityInterface;
import io.temporal.activity.ActivityMethod;

@ActivityInterface
public interface SplitAndTransformHl7LogActivity {
    @ActivityMethod
    SplitAndTransformHl7LogOutput splitAndTransformHl7Log(SplitAndTransformHl7LogInput input);

    @ActivityMethod
    WriteHl7FilePathsFileOutput writeHl7FilePathsFile(WriteHl7FilePathsFileInput input);
}
