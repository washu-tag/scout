package edu.washu.tag.extractor.hl7log.activity;

import edu.washu.tag.extractor.hl7log.model.FindHl7FilesInput;
import edu.washu.tag.extractor.hl7log.model.FindHl7FilesOutput;
import io.temporal.activity.ActivityInterface;
import io.temporal.activity.ActivityMethod;

@ActivityInterface
public interface FindHl7Files {
    @ActivityMethod
    FindHl7FilesOutput findHl7FilesAndWriteManifest(FindHl7FilesInput input);
}
