package edu.washu.tag.extractor.hl7log.activity;

import edu.washu.tag.extractor.hl7log.model.ContinueIngestWorkflow;
import edu.washu.tag.extractor.hl7log.model.FindHl7LogFileInput;
import edu.washu.tag.extractor.hl7log.model.FindHl7LogFileOutput;
import edu.washu.tag.extractor.hl7log.util.FileHandler;
import io.temporal.activity.Activity;
import io.temporal.activity.ActivityInfo;
import io.temporal.failure.ApplicationFailure;
import io.temporal.spring.boot.ActivityImpl;
import io.temporal.workflow.Workflow;
import java.io.ByteArrayOutputStream;
import java.net.URI;
import java.net.URISyntaxException;
import java.util.Arrays;
import java.util.stream.Stream;
import org.slf4j.Logger;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import java.io.File;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Collections;
import java.util.List;

import static edu.washu.tag.extractor.hl7log.util.Constants.PARENT_QUEUE;

@Component
@ActivityImpl(taskQueues = PARENT_QUEUE)
public class FindHl7LogsActivityImpl implements FindHl7LogsActivity {
    private static final Logger logger = Workflow.getLogger(FindHl7LogsActivityImpl.class);

    @Value("${scout.max-children}")
    private int maxChildren;

    private final FileHandler fileHandler;

    public FindHl7LogsActivityImpl(FileHandler fileHandler) {
        this.fileHandler = fileHandler;
    }


    @Override
    public FindHl7LogFileOutput findHl7LogFiles(FindHl7LogFileInput input) {
        ActivityInfo activityInfo = Activity.getExecutionContext().getInfo();
        logger.info("WorkflowId {} ActivityId {} - Finding HL7 log files for input {}", activityInfo.getWorkflowId(), activityInfo.getActivityId(), input);
        List<String> logFiles;
        if (input.logPaths() != null && !input.logPaths().isEmpty()) {
            // Case 1: We were given explicit log paths
            logFiles = findLogFilesForPaths(input.logPaths());
        } else if (input.logsRootPath() != null && !input.logsRootPath().isEmpty() && input.date() != null && !input.date().isEmpty()) {
            // Case 2: We were given a root path and a date
            logFiles = findLogFileForDate(input.logsRootPath(), input.date());
        } else {
            // Case 3: We have nothing
            logFiles = Collections.emptyList();
        }

        logger.info("WorkflowId {} ActivityId {} - Found {} log files", activityInfo.getWorkflowId(), activityInfo.getActivityId(), logFiles.size());

        if (logFiles.isEmpty()) {
            throw ApplicationFailure.newFailure("No log files found for input " + input, "type");
        }

        // If number of log files is over the limit, we will need to split our workflow into batches and Continue-As-New
        ContinueIngestWorkflow continued = null;
        if (logFiles.size() > maxChildren) {
            continued = writeManifestFile(logFiles, input.manifestFilePath());
            logFiles = logFiles.subList(0, continued.nextIndex());
        }

        return new FindHl7LogFileOutput(logFiles, continued);
    }

    /**
     * Continue the ingest workflow with the next batch of log files.
     *
     * @param input The input to continue the ingest workflow.
     * @return The next batch of log files to process. Its continued field will be null if this is the final batch.
     */
    @Override
    public FindHl7LogFileOutput continueIngestHl7LogWorkflow(ContinueIngestWorkflow input) {
        ActivityInfo activityInfo = Activity.getExecutionContext().getInfo();

        String manifestFilePath = input.manifestFilePath();

        URI manifestFileUri;
        try {
            manifestFileUri = new URI(manifestFilePath);
        } catch (URISyntaxException e) {
            throw ApplicationFailure.newFailureWithCause("Manifest file path " + manifestFilePath + " is not a URI", "type", e);
        }

        // Read the manifest file
        logger.info("WorkflowId {} ActivityId {} - Reading manifest file {}", activityInfo.getWorkflowId(), activityInfo.getActivityId(), manifestFilePath);
        String manifest;
        try {
            byte[] manifestBytes = fileHandler.read(manifestFileUri);
            manifest = new String(manifestBytes);
        } catch (IOException e) {
            throw ApplicationFailure.newFailureWithCause("Failed to read manifest file " + manifestFilePath, "type", e);
        }

        // Get the next batch of log files to process
        List<String> logFiles = Arrays.stream(manifest.split("\n"))
            .skip(input.nextIndex())  // Skip forward to the next index
            .limit(maxChildren) // Return the next maxChildren files to process
            .toList();

        // Do we continue again or are we at the end?
        ContinueIngestWorkflow continued = null;
        int nextIndex = input.nextIndex() + maxChildren;
        if (nextIndex < input.numLogFiles()) {
            logger.info("WorkflowId {} ActivityId {} - We will continue the workflow as new starting with index {}",
                activityInfo.getWorkflowId(), activityInfo.getActivityId(), nextIndex);
            continued = new ContinueIngestWorkflow(manifestFilePath, input.numLogFiles(), nextIndex);
        } else {
            logger.info("WorkflowId {} ActivityId {} - nextIndex {} >= numLogFiles {}. This batch is the end of the log files.",
                activityInfo.getWorkflowId(), activityInfo.getActivityId(), nextIndex, input.numLogFiles());
        }
        return new FindHl7LogFileOutput(logFiles, continued);
    }

    /**
     * Find log files for the given paths.
     * If the path is a directory, find all files in the directory recursively
     *
     * @param logPaths list of paths to search for log files
     * @return list of log files
     */
    private List<String> findLogFilesForPaths(List<String> logPaths) {
        ActivityInfo activityInfo = Activity.getExecutionContext().getInfo();
        return logPaths.stream()
                .map(logPath -> {
                    logger.info(
                            "WorkflowId {} ActivityId {} - Finding HL7 log files for path {}",
                            activityInfo.getWorkflowId(), activityInfo.getActivityId(), logPath
                    );
                    File logFile = new File(logPath);
                    if (logFile.isDirectory()) {
                        try {
                            return Files.walk(logFile.toPath())
                                    .filter(Files::isRegularFile)
                                    .map(Path::toString)
                                    .filter(s -> s.endsWith(".log"))
                                    .toList();
                        } catch (IOException e) {
                            logger.warn(
                                    "WorkflowId {} ActivityId {} - Error finding log files in directory {}",
                                    activityInfo.getWorkflowId(), activityInfo.getActivityId(), logPath, e
                            );
                            return Collections.<String>emptyList();
                        }
                    } else {
                        return List.of(logPath);
                    }
                })
                .flatMap(List::stream)
                .toList();
    }

    private List<String> findLogFileForDate(String logsRootPath, String date) {
        ActivityInfo activityInfo = Activity.getExecutionContext().getInfo();
        logger.info(
                "WorkflowId {} ActivityId {} - Finding HL7 log file for date {} in root path {}",
                activityInfo.getWorkflowId(), activityInfo.getActivityId(), date, logsRootPath
        );
        try (Stream<Path> walk = Files.walk(Path.of(logsRootPath))) {
            return walk
                    .filter(Files::isRegularFile)
                    .filter(path -> path.getFileName().toString().contains(date))
                    .map(Path::toString)
                    .filter(s -> s.endsWith(".log"))
                    .toList();
        } catch (IOException e) {
            throw ApplicationFailure.newFailure("Error finding log file for date " + date + " in root path " + logsRootPath, "type", e);
        }
    }

    /**
     * Write the full list of log files into a manifest file
     * @param logFiles Full list of log files to process
     * @param manifestFilePath Path to the manifest file
     * @return ContinueIngestWorkflow object with the manifest file path and the next index
     */
    private ContinueIngestWorkflow writeManifestFile(List<String> logFiles, String manifestFilePath) {
        // Write out the full list into a manifest file
        URI manifestFileUri;
        try {
            manifestFileUri = new URI(manifestFilePath);
        } catch (URISyntaxException e) {
            throw ApplicationFailure.newFailureWithCause("Manifest file path " + manifestFilePath + " is not a URI", "type", e);
        }

        // Upload the file to the scratch space
        try (ByteArrayOutputStream outputStream = new ByteArrayOutputStream()) {
            for (String logFile : logFiles) {
                outputStream.write(logFile.getBytes());
                outputStream.write('\n');
            }
            fileHandler.putWithRetry(outputStream.toByteArray(), manifestFileUri);
        } catch (IOException e) {
            throw ApplicationFailure.newFailureWithCause("Could not write manifest file to  " + manifestFilePath, "type", e);
        }

        return new ContinueIngestWorkflow(manifestFilePath, logFiles.size(), maxChildren);
    }
}
