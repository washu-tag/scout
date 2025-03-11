package edu.washu.tag.temporal.activity;

import edu.washu.tag.temporal.exception.FileFormatException;
import edu.washu.tag.temporal.model.SplitHl7LogActivityInput;
import edu.washu.tag.temporal.model.SplitHl7LogActivityOutput;
import edu.washu.tag.temporal.model.TransformSplitHl7LogInput;
import edu.washu.tag.temporal.model.TransformSplitHl7LogOutput;
import edu.washu.tag.temporal.model.WriteHl7FilePathsFileInput;
import edu.washu.tag.temporal.model.WriteHl7FilePathsFileOutput;
import edu.washu.tag.temporal.util.FileHandler;
import edu.washu.tag.temporal.util.Hl7LogSplitter;
import edu.washu.tag.temporal.util.Hl7LogTransformer;
import io.temporal.activity.Activity;
import io.temporal.activity.ActivityInfo;
import io.temporal.failure.ApplicationFailure;
import io.temporal.spring.boot.ActivityImpl;
import io.temporal.workflow.Workflow;
import org.slf4j.Logger;
import org.springframework.stereotype.Component;

import java.io.IOException;
import java.net.URI;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

import static edu.washu.tag.temporal.util.Constants.CHILD_QUEUE;

@Component
@ActivityImpl(taskQueues = CHILD_QUEUE)
public class SplitHl7LogActivityImpl implements SplitHl7LogActivity {

    private static final Logger logger = Workflow.getLogger(SplitHl7LogActivityImpl.class);

    // Autowire FileHandler
    private final FileHandler fileHandler;

    public SplitHl7LogActivityImpl(FileHandler fileHandler) {
        this.fileHandler = fileHandler;
    }

    @Override
    public SplitHl7LogActivityOutput splitHl7Log(SplitHl7LogActivityInput input) {
        ActivityInfo activityInfo = Activity.getExecutionContext().getInfo();
        logger.info("WorkflowId {} ActivityId {} - Splitting HL7 log file {}", activityInfo.getWorkflowId(), activityInfo.getActivityId(), input.logPath());
        ActivityInfo info = Activity.getExecutionContext().getInfo();

        URI destination = URI.create(input.rootOutputPath());

        String tempdirPrefix = "split-hl7-log-" + info.getWorkflowId() + "-" + info.getActivityId();
        Path tempdir;
        try {
            tempdir = Files.createTempDirectory(tempdirPrefix);
        } catch (IOException e) {
            throw ApplicationFailure.newFailureWithCause("Could not create temp directory", "type", e);
        }

        List<Path> absolutePaths;
        try {
            absolutePaths = Hl7LogSplitter.splitLogFile(input.logPath(), tempdir);
        } catch (IOException e) {
            throw ApplicationFailure.newFailureWithCause("Failed to split log file " + input.logPath(), "type", e);
        }

        List<String> destinationPaths;
        try {
            destinationPaths = fileHandler.put(absolutePaths, tempdir, destination);
        } catch (IOException e) {
            throw ApplicationFailure.newFailureWithCause("Could not put files to " + destination, "type", e);
        }

        deleteTempDirectory(tempdir, activityInfo);

        return new SplitHl7LogActivityOutput(destinationPaths);
    }

    @Override
    public TransformSplitHl7LogOutput transformSplitHl7Log(TransformSplitHl7LogInput input) {
        ActivityInfo activityInfo = Activity.getExecutionContext().getInfo();
        logger.info(
                "WorkflowId {} ActivityId {} - Transforming split HL7 log file {}",
                activityInfo.getWorkflowId(), activityInfo.getActivityId(), input.splitLogFile()
        );
        ActivityInfo info = Activity.getExecutionContext().getInfo();

        URI destination = URI.create(input.rootOutputPath());

        String tempdirPrefix = "transform-split-hl7-log-" + info.getWorkflowId() + "-" + info.getActivityId();
        Path tempdir;
        try {
            tempdir = Files.createTempDirectory(tempdirPrefix);
        } catch (IOException e) {
            throw ApplicationFailure.newFailureWithCause("Could not create temp directory", "type", e);
        }

        // Download input file
        Path localFile;
        try {
            localFile = fileHandler.get(URI.create(input.splitLogFile()), tempdir);
        } catch (IOException e) {
            throw ApplicationFailure.newFailureWithCause("Could not get input file " + input.splitLogFile(), "type", e);
        }

        Path absolutePath;
        try {
            absolutePath = Hl7LogTransformer.transformLogFile(localFile, tempdir);
        } catch (IOException | FileFormatException e) {
            throw ApplicationFailure.newFailureWithCause("Could not transform split file " + localFile + " to HL7", "type", e);
        }

        String destinationPath;
        try {
            destinationPath = fileHandler.put(absolutePath, tempdir, destination);
        } catch (IOException e) {
            throw ApplicationFailure.newFailureWithCause("Could not put files to " + destination, "type", e);
        }

        deleteTempDirectory(tempdir, activityInfo);

        return new TransformSplitHl7LogOutput(input.rootOutputPath() + "/" + destinationPath);
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

    private void deleteTempDirectory(Path tempdir, ActivityInfo activityInfo) {
        try {
            fileHandler.deleteDir(tempdir);
        } catch (IOException ignored) {
            logger.warn("WorkflowId {} ActivityId {} - Failed to delete temp dir {}", activityInfo.getWorkflowId(), activityInfo.getActivityId(), tempdir);
        }
    }
}
