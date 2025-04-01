package edu.washu.tag.temporal.activity;

import edu.washu.tag.temporal.model.FindHl7FilesInput;
import edu.washu.tag.temporal.model.FindHl7FilesOutput;
import io.temporal.activity.ActivityInterface;
import io.temporal.activity.ActivityMethod;

@ActivityInterface
public interface FindHl7Files {
    @ActivityMethod
    FindHl7FilesOutput findHl7FilesAndWriteManifest(FindHl7FilesInput input);
}
