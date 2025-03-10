package edu.washu.tag.temporal.activity;

import static edu.washu.tag.temporal.util.Constants.CHILD_QUEUE;

import edu.washu.tag.temporal.exception.FileFormatException;
import edu.washu.tag.temporal.model.SplitAndTransformHl7LogInput;
import edu.washu.tag.temporal.model.SplitAndTransformHl7LogOutput;
import edu.washu.tag.temporal.model.WriteHl7FilePathsFileInput;
import edu.washu.tag.temporal.model.WriteHl7FilePathsFileOutput;
import edu.washu.tag.temporal.util.FileHandler;
import edu.washu.tag.temporal.util.HL7LogSplitter;
import edu.washu.tag.temporal.util.HL7LogTransformer;
import io.temporal.activity.Activity;
import io.temporal.activity.ActivityInfo;
import io.temporal.failure.ApplicationFailure;
import io.temporal.spring.boot.ActivityImpl;
import io.temporal.workflow.Workflow;
import java.io.IOException;
import java.net.URI;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import javax.annotation.Nullable;
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

        // Step 1: Split the log file
        List<Path> splitFilePaths;
        try {
            splitFilePaths = HL7LogSplitter.splitLogFile(input.logPath(), tempdir);
        } catch (IOException e) {
            throw ApplicationFailure.newFailureWithCause("Failed to split log file " + input.logPath(), "type", e);
        }

        // Step 2: In parallel, transform split files and upload to S3
        List<String> hl7Paths = transformAndUpload(activityInfo, splitFilePaths, tempdir, destination);

        deleteTempDir(activityInfo, tempdir);

        if (hl7Paths.isEmpty()) {
            // All the transforms/uploads failed, fail the activity
            logger.error("WorkflowId {} ActivityId {} - All transform and upload jobs failed for log {}", activityInfo.getWorkflowId(),
                activityInfo.getActivityId(), input.logPath());
            throw ApplicationFailure.newFailure("Transform and upload task failed", "type");
        }

        return new SplitAndTransformHl7LogOutput(hl7Paths);
    }

    private List<String> transformAndUpload(ActivityInfo activityInfo, List<Path> splitFilePaths, Path tempdir, URI destination) {
        List<String> hl7Paths = new ArrayList<>();
        // Submit transformation tasks
        for (Path splitFilePath : splitFilePaths) {
            hl7Paths.add(transformSplitFileAndUpload(activityInfo, splitFilePath, tempdir, destination));
        }
        return hl7Paths;
    }

    private List<String> transformAndUploadInParallel(ActivityInfo activityInfo, List<Path> splitFilePaths, Path tempdir, URI destination) {
        List<String> hl7Paths = new ArrayList<>();
        try (ExecutorService executor = Executors.newVirtualThreadPerTaskExecutor()) {
            List<Future<String>> transformFutures = new ArrayList<>();

            // Submit transformation tasks
            for (Path splitFilePath : splitFilePaths) {
                transformFutures.add(executor.submit(() -> transformSplitFileAndUpload(activityInfo, splitFilePath, tempdir, destination)));
            }

            // Track task completion and collect results
            int completed = 0;
            int total = transformFutures.size();
            for (Future<String> future : transformFutures) {
                try {
                    String hl7Path = future.get(); // This blocks until the task is done
                    completed++;

                    if (completed % 10 == 0 || completed == total) {
                        logger.info("WorkflowId {} ActivityId {} - Transformed and uploaded {}/{} files", activityInfo.getWorkflowId(),
                            activityInfo.getActivityId(), completed, total);
                    }

                    // Add the result to our list of paths
                    if (hl7Path != null) {
                        hl7Paths.add(hl7Path);
                    }
                } catch (ExecutionException ignored) {
                    // Ignore execution exceptions (we shouldn't ever get them since we're catching and logging
                    // within the transformSplitFileAndUpload, and just returning `null`)
                    // We only want to fail this activity if ALL executions fail
                } catch (InterruptedException e) {
                    logger.error("WorkflowId {} ActivityId {} - Transform task failed", activityInfo.getWorkflowId(), activityInfo.getActivityId(), e);
                    throw ApplicationFailure.newFailureWithCause("Transform and upload task interrupted", "type", e);
                }
            }
        }
        return hl7Paths;
    }

    private void deleteTempDir(ActivityInfo activityInfo, Path tempdir) {
        try {
            fileHandler.deleteDir(tempdir);
        } catch (IOException ignored) {
            logger.warn("WorkflowId {} ActivityId {} - Failed to delete temp dir {}", activityInfo.getWorkflowId(), activityInfo.getActivityId(), tempdir);
        }
    }

    @Nullable
    private String transformSplitFileAndUpload(ActivityInfo activityInfo, Path splitFilePath, Path tempdir, URI destination) {

        logger.info("WorkflowId {} ActivityId {} - Transforming split HL7 log file {}", activityInfo.getWorkflowId(), activityInfo.getActivityId(),
            splitFilePath);
        try {
            Path hl7File;
            try {
                hl7File = HL7LogTransformer.transformLogFile(splitFilePath, tempdir);
            } catch (IOException | FileFormatException e) {
                throw ApplicationFailure.newFailureWithCause("Could not transform split file " + splitFilePath + " to HL7", "type", e);
            }

            String destinationPath;
            try {
                destinationPath = fileHandler.putWithRetry(hl7File, tempdir, destination);
            } catch (IOException e) {
                logger.error("\"WorkflowId {} ActivityId {} - Failed to upload {} to {}", activityInfo.getWorkflowId(), activityInfo.getActivityId(), hl7File,
                    destination, e);
                return null;
            }
            return destination.toString() + "/" + destinationPath; // URI#resolve strips trailing path from destination
        } catch (Exception e) {
            logger.error("WorkflowId {} ActivityId {} - Unexpected error during transform and upload of HL7 log file {}", activityInfo.getWorkflowId(), activityInfo.getActivityId(),
                splitFilePath);
            return null;
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
