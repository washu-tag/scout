package edu.washu.tag.temporal.activity;

import edu.washu.tag.temporal.model.SplitHl7LogActivityInput;
import edu.washu.tag.temporal.model.SplitHl7LogActivityOutput;
import edu.washu.tag.temporal.model.TransformSplitHl7LogInput;
import edu.washu.tag.temporal.model.TransformSplitHl7LogOutput;
import edu.washu.tag.temporal.model.WriteHl7FilePathsFileInput;
import edu.washu.tag.temporal.model.WriteHl7FilePathsFileOutput;
import io.temporal.activity.ActivityInterface;
import io.temporal.activity.ActivityMethod;

@ActivityInterface
public interface SplitHl7LogActivity {
    @ActivityMethod
    SplitHl7LogActivityOutput splitHl7Log(SplitHl7LogActivityInput input);

    @ActivityMethod
    TransformSplitHl7LogOutput transformSplitHl7Log(TransformSplitHl7LogInput input);

    @ActivityMethod
    WriteHl7FilePathsFileOutput writeHl7FilePathsFile(WriteHl7FilePathsFileInput input);
}
