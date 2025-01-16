package edu.washu.tag.temporal.activity;

import edu.washu.tag.temporal.model.SplitHl7LogActivityInput;
import edu.washu.tag.temporal.model.SplitHl7LogActivityOutput;
import edu.washu.tag.temporal.model.TransformSplitHl7LogInput;
import edu.washu.tag.temporal.model.TransformSplitHl7LogOutput;
import edu.washu.tag.temporal.util.FileHandler;
import edu.washu.tag.temporal.model.FindHl7LogFileInput;
import edu.washu.tag.temporal.model.FindHl7LogFileOutput;
import io.temporal.activity.Activity;
import io.temporal.activity.ActivityInfo;
import io.temporal.failure.ApplicationFailure;
import io.temporal.spring.boot.ActivityImpl;
import io.temporal.workflow.Workflow;
import org.slf4j.Logger;
import org.springframework.stereotype.Component;

import java.io.File;
import java.io.IOException;
import java.net.URI;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Arrays;
import java.util.List;

@Component
@ActivityImpl(taskQueues = "ingest-hl7-log")
public class SplitHl7LogActivityImpl implements SplitHl7LogActivity {
    private static final Logger logger = Workflow.getLogger(SplitHl7LogActivityImpl.class);

    // Autowire FileHandler
    private final FileHandler fileHandler;

    public SplitHl7LogActivityImpl(FileHandler fileHandler) {
        this.fileHandler = fileHandler;
    }

    private String runScript(File cwd, String... command) {
        String commandName = command.length > 0 ? command[0] : "<unknown>";
        try {
            Process p = new ProcessBuilder()
                    .directory(cwd)
                    .command(command)
                    .start();
            int exitCode = p.waitFor();
            if (exitCode != 0) {
                String stderr = new String(p.getErrorStream().readAllBytes());
                throw ApplicationFailure.newFailure("Command " + commandName + " failed with exit code " + exitCode + ". stderr: " + stderr, "type");
            }
            return new String(p.getInputStream().readAllBytes());
        } catch (IOException | InterruptedException e) {
            throw ApplicationFailure.newFailureWithCause("Command " + commandName + " failed", "type", e);
        }
    }

    @Override
    public FindHl7LogFileOutput findHl7LogFile(FindHl7LogFileInput input) {
        logger.debug("Finding HL7 log file for date {}", input.date());
        File logsDir = Path.of(input.logsDir()).toFile();

        // First try to find file in this dir
        File[] logFiles = logsDir.listFiles((dir, name) -> name.contains(input.date()));
        if (logFiles == null || logFiles.length == 0) {
            // We didn't find file in root dir. Try to find file in a year subdirectory.
            String year = input.date().substring(0, 4);
            File[] yearDirs = logsDir.listFiles((dir, name) -> name.equals(year));
            if (yearDirs != null && yearDirs.length == 1) {
                logFiles = yearDirs[0].listFiles((dir, name) -> name.contains(input.date()));
            }
        }
        if (logFiles == null || logFiles.length != 1) {
            throw ApplicationFailure.newFailure("Expected exactly one file with date " + input.date() + " in " + input.logsDir() + ". Found " + (logFiles == null ? 0 : logFiles.length), "type");
        }

        return new FindHl7LogFileOutput(logFiles[0].getAbsolutePath());
    }

    @Override
    public SplitHl7LogActivityOutput splitHl7Log(SplitHl7LogActivityInput input) {
        logger.debug("Splitting HL7 log file {}", input.logFilePath());
        ActivityInfo info = Activity.getExecutionContext().getInfo();

        URI destination = URI.create(input.rootOutputPath());

        String tempdirPrefix = "split-hl7-log-" + info.getWorkflowId() + "-" + info.getActivityId();
        Path tempdir;
        try {
            tempdir = Files.createTempDirectory(tempdirPrefix);
        } catch (IOException e) {
            throw ApplicationFailure.newFailureWithCause("Could not create temp directory", "type", e);
        }
        // TODO configure the path to the script
        String stdout = runScript(tempdir.toFile(), "/app/scripts/split-hl7-log.sh", input.logFilePath());
        List<Path> relativePaths = Arrays.stream(stdout.split("\n")).map(Path::of).toList();

        List<String> destinationPaths;
        try {
            destinationPaths = fileHandler.put(relativePaths, tempdir, destination);
        } catch (IOException e) {
            throw ApplicationFailure.newFailureWithCause("Could not put files to " + destination, "type", e);
        }

        try {
            fileHandler.deleteDir(tempdir);
        } catch (IOException ignored) {
            logger.warn("Failed to delete temp dir {}", tempdir);
        }

        return new SplitHl7LogActivityOutput(input.rootOutputPath(), destinationPaths);
    }

    @Override
    public TransformSplitHl7LogOutput transformSplitHl7Log(TransformSplitHl7LogInput input) {
        logger.debug("Transforming split HL7 log file {}", input.splitLogFile());
        ActivityInfo info = Activity.getExecutionContext().getInfo();

        URI destination = URI.create(input.rootOutputPath());

        String tempdirPrefix = "transform-split-hl7-log-" + info.getWorkflowId() + "-" + info.getActivityId();
        Path tempdir;
        try {
            tempdir = Files.createTempDirectory(tempdirPrefix);
        } catch (IOException e) {
            throw ApplicationFailure.newFailureWithCause("Could not create temp directory", "type", e);
        }

        // TODO configure the path to the script
        String relativePath = runScript(tempdir.toFile(), "/app/scripts/transform-split-hl7-log.sh", input.splitLogFile());
        String destinationPath;
        try {
            destinationPath = fileHandler.put(Path.of(relativePath), tempdir, destination);
        } catch (IOException e) {
            throw ApplicationFailure.newFailureWithCause("Could not put files to " + destination, "type", e);
        }

        try {
            fileHandler.deleteDir(tempdir);
        } catch (IOException ignored) {
            logger.warn("Failed to delete temp dir {}", tempdir);
        }
        return new TransformSplitHl7LogOutput(input.rootOutputPath(), destinationPath);
    }
}
