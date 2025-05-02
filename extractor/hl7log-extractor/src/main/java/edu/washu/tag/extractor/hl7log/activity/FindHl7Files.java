package edu.washu.tag.extractor.hl7log.activity;

import edu.washu.tag.extractor.hl7log.model.FindHl7FilesInput;
import edu.washu.tag.extractor.hl7log.model.FindHl7FilesOutput;
import edu.washu.tag.extractor.hl7log.model.WriteHl7FilesErrorStatusToDbInput;
import edu.washu.tag.extractor.hl7log.model.WriteHl7FilesErrorStatusToDbOutput;
import io.temporal.activity.ActivityInterface;
import io.temporal.activity.ActivityMethod;

@ActivityInterface
public interface FindHl7Files {
    @ActivityMethod
    FindHl7FilesOutput findHl7FilesAndWriteManifest(FindHl7FilesInput input);

    @ActivityMethod
    WriteHl7FilesErrorStatusToDbOutput writeHl7FilesErrorStatusToDb(WriteHl7FilesErrorStatusToDbInput input);
}
