package edu.washu.tag.temporal.activity;

import edu.washu.tag.temporal.model.IngestHl7FilesToDeltaLakeInput;
import edu.washu.tag.temporal.model.IngestHl7FilesToDeltaLakeOutput;
import io.temporal.activity.ActivityInterface;
import io.temporal.activity.ActivityMethod;

@ActivityInterface
public interface IngestHl7FilesToDeltaLakeActivity {
    @ActivityMethod
    IngestHl7FilesToDeltaLakeOutput ingestHl7FilesToDeltaLake(IngestHl7FilesToDeltaLakeInput input);
}
