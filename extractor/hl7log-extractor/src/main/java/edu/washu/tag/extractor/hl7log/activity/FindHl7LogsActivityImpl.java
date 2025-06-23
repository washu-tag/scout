package edu.washu.tag.extractor.hl7log.activity;

import static edu.washu.tag.extractor.hl7log.util.Constants.PARENT_QUEUE;

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
import java.io.IOException;
import java.net.URI;
import java.net.URISyntaxException;
import java.nio.file.FileVisitResult;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.SimpleFileVisitor;
import java.nio.file.attribute.BasicFileAttributes;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.List;
import java.util.function.Predicate;
import org.slf4j.Logger;
import org.springframework.stereotype.Component;

/**
 * Activity for finding HL7 log files.
 */
@Component
@ActivityImpl(taskQueues = PARENT_QUEUE)
public class FindHl7LogsActivityImpl implements FindHl7LogsActivity {

    private static final Logger logger = Workflow.getLogger(FindHl7LogsActivityImpl.class);

    private final FileHandler fileHandler;

    /**
     * Constructor for FindHl7LogsActivityImpl.
     *
     * @param fileHandler The file handler to manage file operations.
     */
    public FindHl7LogsActivityImpl(FileHandler fileHandler) {
        this.fileHandler = fileHandler;
    }

    /**
     * Finds HL7 log files based on the specified input parameters.
     *
     * @param input The input containing paths and filtering criteria.
     * @return The output containing the list of found log files.
     */
    @Override
    public FindHl7LogFileOutput findHl7LogFiles(FindHl7LogFileInput input) {
        ActivityInfo activityInfo = Activity.getExecutionContext().getInfo();
        logger.info("WorkflowId {} ActivityId {} - Finding HL7 log files for input {}", activityInfo.getWorkflowId(), activityInfo.getActivityId(), input);
        List<String> logFiles = findLogFiles(input.logPaths(), input.date());

        logger.info("WorkflowId {} ActivityId {} - Found {} log files", activityInfo.getWorkflowId(), activityInfo.getActivityId(), logFiles.size());

        if (logFiles.isEmpty()) {
            throw ApplicationFailure.newFailure("No log files found for input " + input, "type");
        }

        // If number of log files is over the limit, we will need to split our workflow into batches and Continue-As-New
        ContinueIngestWorkflow continued = null;
        if (logFiles.size() > input.splitAndUploadConcurrency()) {
            continued = writeManifestFile(logFiles, input.manifestFilePath(), input.splitAndUploadConcurrency());
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
            .limit(input.splitAndUploadConcurrency()) // Return the next maxChildren files to process
            .toList();

        // Do we continue again or are we at the end?
        ContinueIngestWorkflow continued = null;
        int nextIndex = input.nextIndex() + input.splitAndUploadConcurrency();
        if (nextIndex < input.numLogFiles()) {
            logger.info("WorkflowId {} ActivityId {} - We will continue the workflow as new starting with index {}",
                activityInfo.getWorkflowId(), activityInfo.getActivityId(), nextIndex);
            continued = new ContinueIngestWorkflow(manifestFilePath, input.numLogFiles(), nextIndex, input.splitAndUploadConcurrency());
        } else {
            logger.info("WorkflowId {} ActivityId {} - nextIndex {} >= numLogFiles {}. This batch is the end of the log files.",
                activityInfo.getWorkflowId(), activityInfo.getActivityId(), nextIndex, input.numLogFiles());
        }
        return new FindHl7LogFileOutput(logFiles, continued);
    }

    /**
     * Find log files for the given paths. If the path is a directory, find all files in the directory recursively
     *
     * @param logPaths list of paths to search for log files
     * @return list of log files
     */
    private List<String> findLogFiles(List<String> logPaths, String date) {
        ActivityInfo activityInfo = Activity.getExecutionContext().getInfo();

        // Filter log files by date if provided
        Predicate<String> dateFilter;
        if (date != null && !date.isBlank()) {
            logger.info(
                "WorkflowId {} ActivityId {} - Finding HL7 log file by date {}",
                activityInfo.getWorkflowId(), activityInfo.getActivityId(), date
            );
            dateFilter = fileName -> fileName.contains(date);
        } else {
            logger.info(
                "WorkflowId {} ActivityId {} - Finding all HL7 log files in input paths",
                activityInfo.getWorkflowId(), activityInfo.getActivityId()
            );
            dateFilter = fileName -> true;
        }

        // Also exclude hidden files, non-log files
        Predicate<String> logFileFilter = fileName -> !fileName.startsWith(".") && fileName.endsWith(".log") && dateFilter.test(fileName);

        return logPaths.stream()
            .map(logPathString -> {
                logger.info(
                    "WorkflowId {} ActivityId {} - Finding HL7 log files for path {}",
                    activityInfo.getWorkflowId(), activityInfo.getActivityId(), logPathString
                );
                Path logPath = Paths.get(logPathString);
                List<String> matchingLogs = new ArrayList<>();
                if (Files.isDirectory(logPath)) {
                    try {
                        Files.walkFileTree(logPath, new SimpleFileVisitor<>() {
                            @Override
                            public FileVisitResult visitFile(Path path, BasicFileAttributes attrs) {
                                String fileName = path.getFileName().toString();

                                if (attrs.isRegularFile() && logFileFilter.test(fileName)) {
                                    matchingLogs.add(path.toString());
                                }
                                return FileVisitResult.CONTINUE;
                            }

                            @Override
                            public FileVisitResult visitFileFailed(Path file, IOException innerException) {
                                logger.warn(
                                    "WorkflowId {} ActivityId {} - Error accessing file {} during directory walk",
                                    activityInfo.getWorkflowId(), activityInfo.getActivityId(),
                                    file, innerException
                                );
                                return FileVisitResult.CONTINUE;
                            }
                        });
                    } catch (IOException e) {
                        logger.warn(
                            "WorkflowId {} ActivityId {} - Error finding log files in directory {}",
                            activityInfo.getWorkflowId(), activityInfo.getActivityId(), logPathString, e
                        );
                        return Collections.<String>emptyList();
                    }
                } else {
                    // If a user has passed a file, don't filter for anything but date (if they've passed a file plus a mismatched date,
                    // they should get an error)
                    String fileName = logPath.getFileName().toString();
                    if (dateFilter.test(fileName)) {
                        matchingLogs.add(logPath.toString());
                    }
                }
                return matchingLogs;
            })
            .flatMap(List::stream)
            .toList();
    }

    /**
     * Write the full list of log files into a manifest file.
     *
     * @param logFiles                  Full list of log files to process
     * @param manifestFilePath          Path to the manifest file
     * @param splitAndUploadConcurrency Number of concurrent split and upload jobs
     * @return ContinueIngestWorkflow object with the manifest file path and the next index
     */
    private ContinueIngestWorkflow writeManifestFile(List<String> logFiles, String manifestFilePath, int splitAndUploadConcurrency) {
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

        return new ContinueIngestWorkflow(manifestFilePath, logFiles.size(), splitAndUploadConcurrency, splitAndUploadConcurrency);
    }
}
