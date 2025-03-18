package edu.washu.tag.temporal.activity;

import edu.washu.tag.temporal.model.Hl7ManifestFileInput;
import edu.washu.tag.temporal.model.Hl7ManifestFileOutput;
import edu.washu.tag.temporal.model.SplitAndTransformHl7LogInput;
import edu.washu.tag.temporal.model.SplitAndTransformHl7LogOutput;
import edu.washu.tag.temporal.model.WriteHl7FilePathsFileInput;
import edu.washu.tag.temporal.model.WriteHl7FilePathsFileOutput;
import io.temporal.activity.ActivityInterface;
import io.temporal.activity.ActivityMethod;

@ActivityInterface
public interface SplitHl7LogActivity {
    @ActivityMethod
    SplitAndTransformHl7LogOutput splitAndTransformHl7Log(SplitAndTransformHl7LogInput input);

    @ActivityMethod
    Hl7ManifestFileOutput writeHl7ManifestFile(Hl7ManifestFileInput input);
}
