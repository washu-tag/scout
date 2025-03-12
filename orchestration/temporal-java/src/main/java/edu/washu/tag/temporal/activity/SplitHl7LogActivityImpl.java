package edu.washu.tag.temporal.activity;

import static edu.washu.tag.temporal.util.Constants.CHILD_QUEUE;

import edu.washu.tag.temporal.exception.FileFormatException;
import edu.washu.tag.temporal.model.SplitAndTransformHl7LogInput;
import edu.washu.tag.temporal.model.SplitAndTransformHl7LogOutput;
import edu.washu.tag.temporal.model.WriteHl7FilePathsFileInput;
import edu.washu.tag.temporal.model.WriteHl7FilePathsFileOutput;
import edu.washu.tag.temporal.util.FileHandler;
import io.temporal.activity.Activity;
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
import java.util.ArrayList;
import java.util.List;
import org.slf4j.Logger;
import org.springframework.stereotype.Component;

@Component
@ActivityImpl(taskQueues = CHILD_QUEUE)
public class SplitHl7LogActivityImpl implements SplitHl7LogActivity {

    private static final Logger logger = Workflow.getLogger(SplitHl7LogActivityImpl.class);
    // Constants
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
    // Autowire FileHandler
    private final FileHandler fileHandler;

    public SplitHl7LogActivityImpl(FileHandler fileHandler) {
        this.fileHandler = fileHandler;
    }

    @Override
    public SplitAndTransformHl7LogOutput splitAndTransformHl7Log(SplitAndTransformHl7LogInput input) {
        ActivityInfo activityInfo = Activity.getExecutionContext().getInfo();
        logger.info("WorkflowId {} ActivityId {} - Splitting HL7 log file {} into component HL7 files", activityInfo.getWorkflowId(),
            activityInfo.getActivityId(), input.logPath());

        URI destination = URI.create(input.rootOutputPath());

        List<String> hl7Paths;
        try {
            hl7Paths = processLogFile(input.logPath(), destination);
        } catch (IOException e) {
            throw ApplicationFailure.newFailureWithCause("Failed to split log file " + input.logPath() + " into HL7s", "type", e);
        }

        if (hl7Paths.isEmpty()) {
            // All the transforms/uploads failed, fail the activity
            logger.error("WorkflowId {} ActivityId {} - All transform and upload jobs failed for log {}", activityInfo.getWorkflowId(),
                activityInfo.getActivityId(), input.logPath());
            throw ApplicationFailure.newFailure("Transform and upload task failed", "type");
        }

        return new SplitAndTransformHl7LogOutput(hl7Paths);
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

    /**
     * Extracts a timestamp from the first lines of a segment.
     *
     * @param lines      Content of split file
     * @param sourceInfo Source information for error reporting
     * @return The extracted timestamp
     * @throws FileFormatException If timestamp extraction fails
     */
    private String extractTimestamp(List<String> lines, String sourceInfo) throws FileFormatException {
        String headerLine = lines.getFirst();

        // Check if we have enough bytes
        if (headerLine.length() < HEADER_LENGTH) {
            throw new FileFormatException(
                String.format("Header is too short, expected at least %d bytes but got %d - %s",
                    HEADER_LENGTH, headerLine.length(), sourceInfo)
            );
        }

        // Extract the timestamp from the header
        return parseAndValidateTimestamp(headerLine.substring(0, HEADER_LENGTH), sourceInfo);
    }

    /**
     * Parses a timestamp from the header string.
     *
     * @param headerStr  Header content as a string
     * @param sourceInfo Source information for error reporting
     * @return The parsed timestamp
     * @throws FileFormatException If the timestamp cannot be parsed or is invalid
     */
    private String parseAndValidateTimestamp(String headerStr, String sourceInfo) throws FileFormatException {
        // Remove all non-digit characters
        String digitsOnly = headerStr.replaceAll("\\D", "");

        if (digitsOnly.isEmpty()) {
            throw new FileFormatException("Could not find any digits in header from " + sourceInfo);
        }

        // Validate timestamp length
        if (digitsOnly.length() < MIN_TIMESTAMP_LENGTH) {
            throw new FileFormatException(
                String.format("Timestamp \"%s\" from %s is not long enough. Minimum length %d, expected length %d.",
                    digitsOnly, sourceInfo, MIN_TIMESTAMP_LENGTH, EXPECTED_TIMESTAMP_LENGTH)
            );
        }

        return digitsOnly;
    }

    /**
     * Splits and transforms an HL7 log file into multiple HL7 formatted files
     *
     * @param logFile     The HL7 log file to process
     * @param destination The URI destination
     * @return List of generated output file paths
     * @throws IOException If an I/O error occurs
     */
    private List<String> processLogFile(String logFile, URI destination) throws IOException {
        Path logFilePath = Paths.get(logFile);

        // Check if file exists
        if (!Files.exists(logFilePath) || !Files.isRegularFile(logFilePath)) {
            throw new IllegalArgumentException("File not found: " + logFilePath);
        }

        List<String> outputPaths = new ArrayList<>();
        try (BufferedReader reader = Files.newBufferedReader(logFilePath, StandardCharsets.ISO_8859_1)) {
            List<String> splitContent = new ArrayList<>();
            String line;
            int hl7Count = 0;

            while ((line = reader.readLine()) != null) {
                // If line is empty, we're at the end of the current HL7 content
                if (line.isEmpty()) {
                    if (!splitContent.isEmpty()) {
                        transformToHl7AndUpload(outputPaths, splitContent, destination, logFilePath, hl7Count++);
                        splitContent.clear();
                    }
                } else {
                    splitContent.add(line);
                }
            }

            // Transform last chunk of content
            if (!splitContent.isEmpty()) {
                transformToHl7AndUpload(outputPaths, splitContent, destination, logFilePath, hl7Count);
            }
        }

        return outputPaths;
    }

    /**
     * Transform split log content into HL7, upload to S3, and add S3 path to list upon success. Log failures so as not to fail the whole operation
     *
     * @param outputPaths List to collect paths
     * @param lines       Content of the split log
     * @param destination Base output location
     * @param logFilePath Path to parent log file for error reporting
     * @param hl7Count    Pointer to this HL7 file's index within parent log for error reporting
     */
    private void transformToHl7AndUpload(List<String> outputPaths, List<String> lines, URI destination, Path logFilePath, int hl7Count) {
        String sourceInfo = logFilePath + " (section " + hl7Count + ")";
        try {
            String outputPath = transformAndUpload(lines, destination, sourceInfo);
            outputPaths.add(outputPath);
        } catch (IOException | FileFormatException e) {
            logger.error("Unable to transform to HL7 {}", sourceInfo, e);
        }
    }

    /**
     * Transform split log content into HL7 and upload to S3
     *
     * @param lines       Content of the split log
     * @param destination Base output location
     * @param sourceInfo  Source information for error reporting
     * @return Path to the created output file
     * @throws IOException         If an I/O error occurs
     * @throws FileFormatException If the split content is invalid
     */
    private String transformAndUpload(List<String> lines, URI destination, String sourceInfo)
        throws IOException, FileFormatException {

        // Need at least 3 lines (2 header lines + content)
        if (lines.size() < 3) {
            throw new FileFormatException("Split content has fewer than 3 lines: " + sourceInfo);
        }

        // Extract timestamp from the segment's first HEADER_LENGTH bytes
        String timestamp = extractTimestamp(lines, sourceInfo);

        // Define output path
        String relativePath = getTimestampPath(timestamp).resolve(timestamp + ".hl7").toString();
        try (ByteArrayOutputStream outputStream = new ByteArrayOutputStream()) {
            // Start from the third line (exclude header)
            for (int i = 2; i < lines.size(); i++) {
                String processedLine = lines.get(i).replaceAll("<R>$", "");

                // Write line with carriage return (HL7 requirement)
                outputStream.write(processedLine.getBytes(StandardCharsets.UTF_8));
                outputStream.write('\r');
            }

            // Convert to byte array and upload to S3
            // We could use piped streams to avoid loading the whole thing into memory, but this adds complexity that isn't warranted for these small files
            return fileHandler.putWithRetry(outputStream.toByteArray(), relativePath, destination);
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
}
