package edu.washu.tag.temporal.activity;

import edu.washu.tag.temporal.model.SplitHl7LogActivityInput;
import edu.washu.tag.temporal.model.SplitHl7LogActivityOutput;
import edu.washu.tag.temporal.model.TransformSplitHl7LogInput;
import edu.washu.tag.temporal.model.TransformSplitHl7LogOutput;
import edu.washu.tag.temporal.model.FindHl7LogFileInput;
import edu.washu.tag.temporal.model.FindHl7LogFileOutput;
import io.temporal.spring.boot.ActivityImpl;

@ActivityImpl(workers = "split-hl7-log-worker")
public class SplitHl7LogActivityImpl implements SplitHl7LogActivity {
    @Override
    public FindHl7LogFileOutput findHl7LogFile(FindHl7LogFileInput input) {
        return null;
    }

    @Override
    public SplitHl7LogActivityOutput splitHl7Log(SplitHl7LogActivityInput input) {
        return null;
    }

    @Override
    public TransformSplitHl7LogOutput transformSplitHl7Log(TransformSplitHl7LogInput input) {
        return null;
    }
}
