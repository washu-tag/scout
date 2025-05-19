package edu.washu.tag.extractor.hl7log.activity;

import static edu.washu.tag.extractor.hl7log.util.Constants.CHILD_QUEUE;

import edu.washu.tag.extractor.hl7log.db.DbUtils.FileStatusStatus;
import edu.washu.tag.extractor.hl7log.db.DbUtils.FileStatusType;
import edu.washu.tag.extractor.hl7log.db.FileStatus;
import edu.washu.tag.extractor.hl7log.db.IngestDbService;
import edu.washu.tag.extractor.hl7log.exception.FileFormatException;
import edu.washu.tag.extractor.hl7log.model.Hl7ManifestFileInput;
import edu.washu.tag.extractor.hl7log.model.Hl7ManifestFileOutput;
import edu.washu.tag.extractor.hl7log.model.SplitAndTransformHl7LogInput;
import edu.washu.tag.extractor.hl7log.model.SplitAndTransformHl7LogOutput;
import edu.washu.tag.extractor.hl7log.util.FileHandler;
import io.temporal.activity.Activity;
import io.temporal.activity.ActivityExecutionContext;
import io.temporal.activity.ActivityInfo;
import io.temporal.failure.ApplicationFailure;
import io.temporal.spring.boot.ActivityImpl;
import io.temporal.workflow.Workflow;
import java.io.BufferedReader;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.time.LocalDate;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import org.apache.commons.lang3.StringUtils;
import org.slf4j.Logger;
import org.springframework.stereotype.Component;

@Component
@ActivityImpl(taskQueues = CHILD_QUEUE)
public class SplitHl7LogActivityImpl implements SplitHl7LogActivity {

    private static final Logger logger = Workflow.getLogger(SplitHl7LogActivityImpl.class);
    // Constants
    private static final String HL7_PATHS_FILENAME = "hl7-paths.txt";
    private static final int HEADER_LENGTH = 24;
    private static final int MIN_TIMESTAMP_LENGTH = 14;
    private static final int EXPECTED_TIMESTAMP_LENGTH = 18;
    // Timestamp position indices
    private static final int YEAR_START = 0;
    private static final int YEAR_END = 4;
    private static final int MONTH_START = 4;
    private static final int MONTH_END = 6;
    private static final int DAY_START = 6;
    private static final int DAY_END = 8;
    private static final int HOUR_START = 8;
    private static final int HOUR_END = 10;

    // Log file date pattern
    private static final Pattern DATE_PATTERN = Pattern.compile("\\d{8}");
    private static final DateTimeFormatter DATE_FORMATTER = DateTimeFormatter.ofPattern("yyyyMMdd");

    private final FileHandler fileHandler;
    private final IngestDbService ingestDbService;

    public SplitHl7LogActivityImpl(FileHandler fileHandler, IngestDbService ingestDbService) {
        this.fileHandler = fileHandler;
        this.ingestDbService = ingestDbService;
    }

    private static LocalDate dateFromLogFilePath(String filePath) {
        String fileName = Paths.get(filePath).getFileName().toString();
        Matcher m = DATE_PATTERN.matcher(fileName);
        if (m.find()) {
            return LocalDate.parse(m.group(), DATE_FORMATTER);
        }
        return null;
    }

    @Override
    public SplitAndTransformHl7LogOutput splitAndTransformHl7Log(SplitAndTransformHl7LogInput input) {
        ActivityExecutionContext ctx = Activity.getExecutionContext();
        ActivityInfo activityInfo = ctx.getInfo();
        String workflowId = activityInfo.getWorkflowId();
        String activityId = activityInfo.getActivityId();

        ctx.heartbeat("Starting");
        logger.info("WorkflowId {} ActivityId {} - Splitting HL7 log file {} into component HL7 files", workflowId, activityId, input.logPath());

        URI destination = URI.create(input.hl7OutputPath());
        List<FileStatus> fileStatuses;
        try {
            ctx.heartbeat("Updating status");
            fileStatuses = processLogFile(input.logPath(), destination);
            ingestDbService.insertFileStatus(FileStatus.parsed(input.logPath(), workflowId, activityId));
        } catch (Exception e) {
            logger.error("WorkflowId {} ActivityId {} - Could not read log file {}",
                activityInfo.getWorkflowId(), activityInfo.getActivityId(), input.logPath(), e);
            ingestDbService.insertFileStatus(
                FileStatus.failed(input.logPath(), FileStatusType.LOG, String.format("%s: %s", e.getClass().getSimpleName(), e.getMessage()), workflowId,
                    activityId));
            throw ApplicationFailure.newFailureWithCause("Could not read log file " + input.logPath(), "type", e);
        }

        // If we parsed no HL7 messages, fail the activity
        if (fileStatuses.size() == 1 && fileStatuses.getFirst().type().equals(FileStatusType.LOG.getType())
            && fileStatuses.getFirst().status().equals(FileStatusStatus.FAILED.getStatus())) {
            // Write the log file status to the database
            ingestDbService.insertFileStatus(fileStatuses.getFirst());
            // Fail the activity
            logger.error("WorkflowId {} ActivityId {} - No HL7 messages found in log file {}", workflowId, activityId, input.logPath());
            throw ApplicationFailure.newFailure("No HL7 messages found in log file", "type");
        }

        // Insert the HL7 file paths into the database
        ctx.heartbeat("Processed " + fileStatuses.size() + " messages");
        ingestDbService.batchInsertNewHl7FileStatuses(fileStatuses, input.logPath(), dateFromLogFilePath(input.logPath()));

        // If all HL7 files failed, fail the activity
        List<String> hl7Paths = fileStatuses.stream()
            .filter(FileStatus::wasStaged)
            .map(FileStatus::filePath)
            .toList();
        if (fileStatuses.isEmpty() || hl7Paths.isEmpty()) {
            // All the transforms/uploads failed, fail the activity
            logger.error("WorkflowId {} ActivityId {} - All transform and upload jobs failed for log {}", workflowId, activityId, input.logPath());
            throw ApplicationFailure.newFailure("Transform and upload task failed", "type");
        }

        // Write the list of HL7 file paths to a file
        ctx.heartbeat("Writing manifest file");
        String hl7ListFilePath = String.join("/",
            input.scratchDir(),
            activityInfo.getWorkflowId(),
            activityInfo.getActivityId() + "_" + HL7_PATHS_FILENAME);
        URI hl7ListFileUri = URI.create(hl7ListFilePath);
        logger.info("WorkflowId {} ActivityId {} - Uploading log file list to {}", workflowId, activityId, hl7ListFileUri);
        String uploadedList;
        try {
            uploadedList = uploadHl7PathList(hl7Paths, hl7ListFileUri);
        } catch (Exception e) {
            logger.error("WorkflowId {} ActivityId {} - Failed to upload log file list to {}",
                activityInfo.getWorkflowId(), activityInfo.getActivityId(), hl7ListFileUri, e);
            throw ApplicationFailure.newFailureWithCause("Failed to upload log file list to " + hl7ListFileUri, "type", e);
        }

        ctx.heartbeat("Completed upload of " + fileStatuses.size() + " messages");
        return new SplitAndTransformHl7LogOutput(uploadedList, hl7Paths.size());
    }

    @Override
    public Hl7ManifestFileOutput writeHl7ManifestFile(Hl7ManifestFileInput input) {
        ActivityInfo activityInfo = Activity.getExecutionContext().getInfo();

        String manifestFilePath = input.manifestFileDirPath() + "/" + activityInfo.getRunId() + "_hl7-manifest.txt";

        // Read all the file path files and collect the HL7 paths
        logger.info("WorkflowId {} ActivityId {} - Reading {} HL7 file path files", activityInfo.getWorkflowId(),
            activityInfo.getActivityId(), input.hl7FilePathFiles().size());
        List<String> hl7Paths = new ArrayList<>();
        for (String hl7FilePathFile : input.hl7FilePathFiles()) {
            try {
                byte[] hl7PathFileBytes = fileHandler.read(URI.create(hl7FilePathFile));
                String hl7PathFileStr = new String(hl7PathFileBytes, StandardCharsets.UTF_8);
                Arrays.stream(hl7PathFileStr.split(System.lineSeparator()))
                    .filter(line -> !line.isEmpty())
                    .forEach(hl7Paths::add);
            } catch (IOException e) {
                logger.warn("WorkflowId {} ActivityId {} - Failed to read HL7 file {}",
                    activityInfo.getWorkflowId(), activityInfo.getActivityId(), hl7FilePathFile, e);
            }
        }

        // Write the manifest file containing all the HL7 paths
        logger.info("WorkflowId {} ActivityId {} - Writing {} HL7 file paths to manifest file {}", activityInfo.getWorkflowId(),
            activityInfo.getActivityId(), hl7Paths.size(), manifestFilePath);
        try {
            byte[] manifestFileBytes = convertListToByteArray(hl7Paths);
            fileHandler.putWithRetry(manifestFileBytes, URI.create(manifestFilePath));
        } catch (IOException e) {
            logger.error("WorkflowId {} ActivityId {} - Failed to write manifest file {}",
                activityInfo.getWorkflowId(), activityInfo.getActivityId(), manifestFilePath, e);
            throw ApplicationFailure.newFailureWithCause("Failed to write manifest file " + manifestFilePath, "type", e);
        }

        logger.info("WorkflowId {} ActivityId {} - Finished writing {} HL7 file paths to manifest file {}. Deleting {} file path files.",
            activityInfo.getWorkflowId(), activityInfo.getActivityId(), hl7Paths.size(), manifestFilePath, input.hl7FilePathFiles().size());

        // Delete the HL7 file path files
        try {
            fileHandler.deleteMultiple(input.hl7FilePathFiles().stream().map(URI::create).toList());
        } catch (Exception e) {
            logger.warn("WorkflowId {} ActivityId {} - Failed to delete HL7 file path files", activityInfo.getWorkflowId(),
                activityInfo.getActivityId(), e);
        }
        logger.info("WorkflowId {} ActivityId {} - Finished deleting {} file path files.", activityInfo.getWorkflowId(),
            activityInfo.getActivityId(), input.hl7FilePathFiles().size());

        return new Hl7ManifestFileOutput(manifestFilePath, hl7Paths.size());
    }

    /**
     * Extracts a timestamp from the line immediately preceding the SB tag
     *
     * @param headerLine Line containing timestamp
     * @return The extracted timestamp
     * @throws FileFormatException If timestamp extraction fails
     */
    private String extractTimestamp(String headerLine) throws FileFormatException {
        // Check if we have enough bytes
        if (headerLine.length() < HEADER_LENGTH) {
            throw new FileFormatException(
                String.format("Timestamp header line is too short, expected at least %d bytes but got %d",
                    HEADER_LENGTH, headerLine.length())
            );
        }

        // Extract the timestamp from the header
        return parseAndValidateTimestamp(headerLine.substring(0, HEADER_LENGTH));
    }

    /**
     * Parses a timestamp from the header string.
     *
     * @param headerStr Header content as a string
     * @return The parsed timestamp
     * @throws FileFormatException If the timestamp cannot be parsed or is invalid
     */
    private String parseAndValidateTimestamp(String headerStr) throws FileFormatException {
        // Remove all non-digit characters
        String digitsOnly = headerStr.replaceAll("\\D", "");

        if (digitsOnly.isEmpty()) {
            throw new FileFormatException("Could not find any digits in timestamp header line");
        }

        // Validate timestamp length
        if (digitsOnly.length() < MIN_TIMESTAMP_LENGTH) {
            throw new FileFormatException(
                String.format("Timestamp \"%s\" is not long enough. Minimum length %d, expected length %d.",
                    digitsOnly, MIN_TIMESTAMP_LENGTH, EXPECTED_TIMESTAMP_LENGTH)
            );
        }

        return digitsOnly;
    }

    /**
     * Splits and transforms an HL7 log file into multiple HL7 formatted files
     *
     * @param logFile     The HL7 log file to process
     * @param destination The URI destination
     * @return List of Hl7File instances containing generated output file paths or error messages
     * @throws IOException If an I/O error occurs
     */
    private List<FileStatus> processLogFile(String logFile, URI destination) throws IOException {
        ActivityExecutionContext ctx = Activity.getExecutionContext();
        ActivityInfo activityInfo = ctx.getInfo();
        String workflowId = activityInfo.getWorkflowId();
        String activityId = activityInfo.getActivityId();

        Path logFilePath = Paths.get(logFile);

        List<FileStatus> results = new ArrayList<>();
        int hl7Count = 0;
        try (BufferedReader reader = Files.newBufferedReader(logFilePath, StandardCharsets.ISO_8859_1)) {
            String line;
            String previousLine = null;
            List<String> hl7Content = new ArrayList<>();

            while ((line = reader.readLine()) != null) {
                if (line.contains("<SB>")) {
                    // Collect lines until the next <EB>
                    while ((line = reader.readLine()) != null) {
                        // Strip the non-HL7 "tags"
                        String processed = line.replaceAll("<R>$", "");

                        // <EB> means we're at the end of the HL7 message. Strip the tag, store any extra content.
                        if (line.contains("<EB>")) {
                            hl7Content.add(processed.replace("<EB>", ""));
                            break;
                        }
                        hl7Content.add(processed);
                    }

                    ctx.heartbeat(hl7Count);
                    results.add(validateWriteAndUploadHl7(logFile, hl7Content, previousLine, destination, hl7Count++, workflowId, activityId));
                    hl7Content.clear();
                    previousLine = null;
                    continue;
                }
                previousLine = line;
            }
        }

        if (hl7Count == 0) {
            results.add(FileStatus.failed(logFile, FileStatusType.LOG, "Log did not contain any HL7 messages", workflowId, activityId));
        }

        return results;
    }

    /**
     * Validate HL7 message has content, write to file, upload to S3, and return status object
     *
     * @param logFile       Source HL7 log file
     * @param lines         Content of the HL7 message, split from the log
     * @param headerLine    Line preceding SB tag
     * @param destination   Base output location
     * @param messageNumber This HL7 file's index within parent log for error reporting
     * @param workflowId    The workflow identifier for logs
     * @param activityId    The activity identifier for logs
     * @return An object containing the file path if the operation was successful, or an error message if not
     */
    private FileStatus validateWriteAndUploadHl7(String logFile, List<String> lines, String headerLine, URI destination, int messageNumber, String workflowId,
        String activityId) {

        if (lines.stream().allMatch(String::isBlank)) {
            return FileStatus.failed(createPlaceholderHl7FilePath(logFile, messageNumber), FileStatusType.HL7,
                "HL7 message content is empty",
                workflowId, activityId);
        }

        if (StringUtils.isBlank(headerLine)) {
            return FileStatus.failed(createPlaceholderHl7FilePath(logFile, messageNumber), FileStatusType.HL7,
                "HL7 content did not contain a timestamp header line; this usually means it is a repeat of the previous message's HL7 content",
                workflowId, activityId);
        }

        String timestamp;
        try {
            timestamp = extractTimestamp(headerLine);
        } catch (FileFormatException e) {
            logger.warn("WorkflowId {} ActivityId {} - Unable to extract timestamp for message {}: {}", workflowId, activityId, messageNumber, e.getMessage());
            return FileStatus.failed(
                createPlaceholderHl7FilePath(logFile, messageNumber),
                FileStatusType.HL7,
                "Unable to extract timestamp for message: " + e.getMessage(),
                workflowId,
                activityId
            );
        }

        try {
            return writeAndUpload(logFile, lines, timestamp, destination, messageNumber, workflowId, activityId);
        } catch (Exception e) {
            logger.error("WorkflowId {} ActivityId {} - Could not write message {} to HL7 file", workflowId, activityId, messageNumber, e);
            return FileStatus.failed(createPlaceholderHl7FilePath(logFile, messageNumber), FileStatusType.HL7,
                "Could not write message to HL7 file: " + e.getMessage(), workflowId, activityId);
        }
    }

    private String createPlaceholderHl7FilePath(String logFile, int messageNumber) {
        return logFile + "_" + messageNumber;
    }

    /**
     * Write HL7 message to file, upload to S3, and return status object
     *
     * @param logFile       Source HL7 log file
     * @param lines         Content of the HL7 message, split from the log
     * @param timestamp     Timestamp for naming the HL7 file
     * @param destination   Base output location in S3
     * @param messageNumber This HL7 file's index within parent log for error reporting
     * @param workflowId    The workflow identifier for logs
     * @param activityId    The activity identifier for logs
     * @return An object containing the file path if the operation was successful, or an error message if not
     * @throws IOException If an I/O error occurs
     */
    private FileStatus writeAndUpload(String logFile, List<String> lines, String timestamp, URI destination, int messageNumber, String workflowId,
        String activityId) throws IOException {

        // Define output path
        String relativePath = getTimestampPath(timestamp).resolve(timestamp + ".hl7").toString();
        logger.info("WorkflowId {} ActivityId {} - Transforming message {} HL7 file {}", workflowId, activityId,
            messageNumber, relativePath);

        try (ByteArrayOutputStream outputStream = new ByteArrayOutputStream()) {
            for (String line : lines) {
                // Write line with carriage return (HL7 requirement)
                outputStream.write(line.getBytes(StandardCharsets.UTF_8));
                outputStream.write('\r');
            }

            logger.info("WorkflowId {} ActivityId {} - Uploading message {} HL7 file {}/{}", workflowId, activityId,
                messageNumber, destination, relativePath);

            try {
                // Convert to byte array and upload to S3
                // We could use piped streams to avoid loading the whole thing into memory, but this adds complexity that isn't warranted for these small files
                String outputPath = fileHandler.putWithRetry(outputStream.toByteArray(), relativePath, destination);
                return FileStatus.staged(outputPath, FileStatusType.HL7, workflowId, activityId);
            } catch (Exception e) {
                logger.error("WorkflowId {} ActivityId {} - Failed to upload message {} HL7 file {}/{}",
                    workflowId, activityId, messageNumber, destination, relativePath, e);
                return FileStatus.failed(createPlaceholderHl7FilePath(logFile, messageNumber), FileStatusType.HL7,
                    "Failed to upload HL7 file", workflowId, activityId);
            }
        }
    }

    private Path getTimestampPath(String timestamp) {
        String year = timestamp.substring(YEAR_START, YEAR_END);
        String month = timestamp.substring(MONTH_START, MONTH_END);
        String day = timestamp.substring(DAY_START, DAY_END);
        String hour = timestamp.substring(HOUR_START, HOUR_END);

        // Create the directory path
        return Path.of(year, month, day, hour);
    }

    private String uploadHl7PathList(List<String> hl7Paths, URI destination) throws IOException {
        return fileHandler.putWithRetry(convertListToByteArray(hl7Paths), destination);
    }

    private byte[] convertListToByteArray(List<String> hl7FilePaths) {
        String content = String.join(System.lineSeparator(), hl7FilePaths);
        return content.getBytes(StandardCharsets.UTF_8);
    }

}
