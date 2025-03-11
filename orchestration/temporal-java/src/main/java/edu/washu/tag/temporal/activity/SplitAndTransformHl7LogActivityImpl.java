package edu.washu.tag.temporal.activity;

import static edu.washu.tag.temporal.util.Constants.CHILD_QUEUE;

import edu.washu.tag.temporal.exception.FileFormatException;
import edu.washu.tag.temporal.model.SplitAndTransformHl7LogInput;
import edu.washu.tag.temporal.model.SplitAndTransformHl7LogOutput;
import edu.washu.tag.temporal.model.WriteHl7FilePathsFileInput;
import edu.washu.tag.temporal.model.WriteHl7FilePathsFileOutput;
import edu.washu.tag.temporal.util.FileHandler;
import edu.washu.tag.temporal.util.Hl7LogProcessor;
import io.temporal.activity.Activity;
import io.temporal.activity.ActivityInfo;
import io.temporal.failure.ApplicationFailure;
import io.temporal.spring.boot.ActivityImpl;
import io.temporal.workflow.Workflow;
import java.io.IOException;
import java.net.URI;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.stream.Collectors;
import org.slf4j.Logger;
import org.springframework.stereotype.Component;

@Component
@ActivityImpl(taskQueues = CHILD_QUEUE)
public class SplitAndTransformHl7LogActivityImpl implements SplitAndTransformHl7LogActivity {

    private static final Logger logger = Workflow.getLogger(SplitAndTransformHl7LogActivityImpl.class);

    // Autowire FileHandler
    private final FileHandler fileHandler;

    public SplitAndTransformHl7LogActivityImpl(FileHandler fileHandler) {
        this.fileHandler = fileHandler;
    }

    @Override
    public SplitAndTransformHl7LogOutput splitAndTransformHl7Log(SplitAndTransformHl7LogInput input) {
        ActivityInfo activityInfo = Activity.getExecutionContext().getInfo();
        logger.info("WorkflowId {} ActivityId {} - Splitting HL7 log file {} into component HL7 files", activityInfo.getWorkflowId(),
            activityInfo.getActivityId(), input.logPath());
        ActivityInfo info = Activity.getExecutionContext().getInfo();

        URI destination = URI.create(input.rootOutputPath());

        String tempdirPrefix = "split-hl7-log-" + info.getWorkflowId() + "-" + info.getActivityId();
        Path tempdir;
        try {
            tempdir = Files.createTempDirectory(tempdirPrefix);
        } catch (IOException e) {
            throw ApplicationFailure.newFailureWithCause("Could not create temp directory", "type", e);
        }

        List<Path> hl7LocalPaths;
        try {
            hl7LocalPaths = Hl7LogProcessor.processLogFile(input.logPath(), tempdir);
        } catch (IOException e) {
            throw ApplicationFailure.newFailureWithCause("Failed to split log file " + input.logPath() + " into HL7s", "type", e);
        }

        List<String> hl7BlobPaths = hl7LocalPaths.stream().map(hl7 -> uploadHl7(hl7, tempdir, destination, activityInfo)).collect(Collectors.toList());

        deleteTempDir(activityInfo, tempdir);

        if (hl7BlobPaths.isEmpty()) {
            // All the transforms/uploads failed, fail the activity
            logger.error("WorkflowId {} ActivityId {} - All transform and upload jobs failed for log {}", activityInfo.getWorkflowId(),
                activityInfo.getActivityId(), input.logPath());
            throw ApplicationFailure.newFailure("Transform and upload task failed", "type");
        }

        return new SplitAndTransformHl7LogOutput(hl7BlobPaths);
    }

    private String uploadHl7(Path hl7File, Path tempdir, URI destination, ActivityInfo activityInfo) {
        String destinationPath;
        try {
            destinationPath = fileHandler.putWithRetry(hl7File, tempdir, destination);
        } catch (IOException e) {
            logger.error("\"WorkflowId {} ActivityId {} - Failed to upload {} to {}", activityInfo.getWorkflowId(), activityInfo.getActivityId(), hl7File,
                destination, e);
            return null;
        }
        return destination.toString() + "/" + destinationPath; // URI#resolve strips trailing path from destination
    }

    private void deleteTempDir(ActivityInfo activityInfo, Path tempdir) {
        try {
            fileHandler.deleteDir(tempdir);
        } catch (IOException ignored) {
            logger.warn("WorkflowId {} ActivityId {} - Failed to delete temp dir {}", activityInfo.getWorkflowId(), activityInfo.getActivityId(), tempdir);
        }
    }

    @Override
    public WriteHl7FilePathsFileOutput writeHl7FilePathsFile(WriteHl7FilePathsFileInput input) {
        ActivityInfo activityInfo = Activity.getExecutionContext().getInfo();
        logger.info("WorkflowId {} ActivityId {} - Writing HL7 file paths file", activityInfo.getWorkflowId(), activityInfo.getActivityId());

        // Create tempdir
        Path tempdir;
        try {
            tempdir = Files.createTempDirectory("write-hl7-file-paths-file-" + activityInfo.getWorkflowId() + "-" + activityInfo.getActivityId());
        } catch (IOException e) {
            throw ApplicationFailure.newFailureWithCause("Could not create temp directory", "type", e);
        }

        String hl7PathsFilename = "hl7-paths.txt";

        // Write hl7 paths to temp file
        Path logPathsFile = tempdir.resolve(hl7PathsFilename);
        try {
            Files.write(logPathsFile, input.hl7FilePaths());
        } catch (IOException e) {
            throw ApplicationFailure.newFailureWithCause("Could not write hl7 paths to file", "type", e);
        }

        // Put file to destination
        URI scratch = URI.create(input.scratchSpacePath());
        try {
            fileHandler.put(logPathsFile, tempdir, scratch);
        } catch (IOException e) {
            throw ApplicationFailure.newFailureWithCause("Could not put hl7 paths file to " + scratch, "type", e);
        }

        // Return absolute path to file
        return new WriteHl7FilePathsFileOutput(input.scratchSpacePath() + "/" + hl7PathsFilename, input.hl7FilePaths().size());
    }
}
