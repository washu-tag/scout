package edu.washu.tag.temporal.activity;

import static edu.washu.tag.temporal.util.Constants.BUILD_MANIFEST_QUEUE;

import edu.washu.tag.temporal.model.FindHl7FilesInput;
import edu.washu.tag.temporal.model.FindHl7FilesOutput;
import edu.washu.tag.temporal.util.FileHandler;
import io.temporal.activity.Activity;
import io.temporal.activity.ActivityInfo;
import io.temporal.failure.ApplicationFailure;
import io.temporal.spring.boot.ActivityImpl;
import io.temporal.workflow.Workflow;
import java.io.IOException;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.util.List;
import org.slf4j.Logger;
import org.springframework.stereotype.Component;

@Component
@ActivityImpl(taskQueues = BUILD_MANIFEST_QUEUE)
public class FindHl7FilesImpl implements FindHl7Files {
    private static final Logger logger = Workflow.getLogger(FindHl7FilesImpl.class);

    private final FileHandler fileHandler;

    public FindHl7FilesImpl(FileHandler fileHandler) {
        this.fileHandler = fileHandler;
    }

    @Override
    public FindHl7FilesOutput findHl7FilesAndWriteManifest(FindHl7FilesInput input) {
        ActivityInfo activityInfo = Activity.getExecutionContext().getInfo();
        logger.info("WorkflowId {} ActivityId {} - Finding HL7 files at root path {}",
            activityInfo.getWorkflowId(), activityInfo.getActivityId(), input.hl7RootPath());

        List<String> hl7Files = fileHandler.ls(URI.create(input.hl7RootPath()));

        if (hl7Files.isEmpty()) {
            throw ApplicationFailure.newFailure("No HL7 files found", "type");
        }

        String manifestFilePath = input.scratchDir() + "/hl7_manifest.txt";
        logger.info("WorkflowId {} ActivityId {} - Writing {} HL7 file paths to manifest file {}",
            activityInfo.getWorkflowId(), activityInfo.getActivityId(), hl7Files.size(), manifestFilePath);
        try {
            String contents = String.join("\n", hl7Files);
            fileHandler.putWithRetry(contents.getBytes(StandardCharsets.UTF_8), URI.create(manifestFilePath));
            return new FindHl7FilesOutput(manifestFilePath);
        } catch (IOException e) {
            throw ApplicationFailure.newFailureWithCause("Error writing HL7 manifest file", "type", e);
        }
    }
}
