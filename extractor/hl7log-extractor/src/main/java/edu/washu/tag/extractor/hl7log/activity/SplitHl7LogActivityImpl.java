package edu.washu.tag.extractor.hl7log.activity;

import static edu.washu.tag.extractor.hl7log.util.Constants.CHILD_QUEUE;

import edu.washu.tag.extractor.hl7log.db.Hl7File;
import edu.washu.tag.extractor.hl7log.db.IngestDbService;
import edu.washu.tag.extractor.hl7log.db.LogFile;
import edu.washu.tag.extractor.hl7log.exception.FileFormatException;
import edu.washu.tag.extractor.hl7log.model.Hl7ManifestFileInput;
import edu.washu.tag.extractor.hl7log.model.Hl7ManifestFileOutput;
import edu.washu.tag.extractor.hl7log.model.SplitAndTransformHl7LogInput;
import edu.washu.tag.extractor.hl7log.model.SplitAndTransformHl7LogOutput;
import edu.washu.tag.extractor.hl7log.util.FileHandler;
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
import java.util.Arrays;
import java.util.List;
import java.util.stream.IntStream;
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

    private final FileHandler fileHandler;
    private final IngestDbService ingestDbService;

    public SplitHl7LogActivityImpl(FileHandler fileHandler, IngestDbService ingestDbService) {
        this.fileHandler = fileHandler;
        this.ingestDbService = ingestDbService;
    }

    @Override
    public SplitAndTransformHl7LogOutput splitAndTransformHl7Log(SplitAndTransformHl7LogInput input) {
        ActivityInfo activityInfo = Activity.getExecutionContext().getInfo();
        logger.info("WorkflowId {} ActivityId {} - Splitting HL7 log file {} into component HL7 files", activityInfo.getWorkflowId(),
            activityInfo.getActivityId(), input.logPath());

        URI destination = URI.create(input.hl7OutputPath());
        List<Hl7File> segmentResults;
        try {
            segmentResults = processLogFile(input.logPath(), destination, activityInfo.getWorkflowId(), activityInfo.getActivityId());

            ingestDbService.insertLogFile(LogFile.success(input.logPath(), activityInfo.getWorkflowId(), activityInfo.getActivityId()));
        } catch (IOException e) {
            logger.error("WorkflowId {} ActivityId {} - Could not read log file {}",
                activityInfo.getWorkflowId(), activityInfo.getActivityId(), input.logPath(), e);
            ingestDbService.insertLogFile(LogFile.error(input.logPath(), e.getMessage(), activityInfo.getWorkflowId(), activityInfo.getActivityId()));
            throw ApplicationFailure.newFailureWithCause("Could not read log file " + input.logPath(), "type", e);
        }

        // Insert the HL7 file paths into the database
        ingestDbService.batchInsertHl7Files(segmentResults);

        // If all HL7 files failed, fail the activity
        List<String> hl7Paths = segmentResults.stream()
            .filter(Hl7File::isSuccess)
            .map(Hl7File::filePath)
            .toList();
        if (segmentResults.isEmpty() || hl7Paths.isEmpty()) {
            // All the transforms/uploads failed, fail the activity
            logger.error("WorkflowId {} ActivityId {} - All transform and upload jobs failed for log {}", activityInfo.getWorkflowId(),
                activityInfo.getActivityId(), input.logPath());
            throw ApplicationFailure.newFailure("Transform and upload task failed", "type");
        }

        // Write the list of HL7 file paths to a file
        String hl7ListFilePath = String.join("/",
            input.scratchDir(),
            activityInfo.getWorkflowId(),
            activityInfo.getActivityId() + "_" + HL7_PATHS_FILENAME);
        URI hl7ListFileUri = URI.create(hl7ListFilePath);
        logger.info("WorkflowId {} ActivityId {} - Uploading log file list to {}", activityInfo.getWorkflowId(),
            activityInfo.getActivityId(), hl7ListFileUri);
        String uploadedList;
        try {
            uploadedList = uploadHl7PathList(hl7Paths, hl7ListFileUri);
        } catch (IOException e) {
            logger.error("WorkflowId {} ActivityId {} - Failed to upload log file list to {}",
                activityInfo.getWorkflowId(), activityInfo.getActivityId(), hl7ListFileUri, e);
            throw ApplicationFailure.newFailureWithCause("Failed to upload log file list to " + hl7ListFileUri, "type", e);
        }

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
            activityInfo.getWorkflowId(),
            activityInfo.getActivityId(), hl7Paths.size(), manifestFilePath, input.hl7FilePathFiles().size());

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
     * Extracts a timestamp from the "header" lines of a segment. Going backward from the SB tag, attempt to find a line with a timestamp we can use
     * to uniquely identify the HL7 report in the segment.
     *
     * @param lines      Content of split file
     * @param sbTagIndex Index of the SB tag in the lines list. All lines prior could contain timestamps
     * @return The extracted timestamp
     * @throws FileFormatException If timestamp extraction fails
     */
    private String extractTimestamp(List<String> lines, int sbTagIndex) throws FileFormatException {
        FileFormatException firstException = null;
        for (int i = sbTagIndex - 1; i >= 0; i--) {
            String line = lines.get(i);

            // Check if we have enough bytes
            if (line.length() < HEADER_LENGTH) {
                if (firstException == null) {
                    firstException = new FileFormatException(
                        String.format("Timestamp header line is too short, expected at least %d bytes but got %d",
                            HEADER_LENGTH, line.length())
                    );
                }
                continue;
            }

            try {
                // Extract the timestamp from the header
                return parseAndValidateTimestamp(line.substring(0, HEADER_LENGTH));
            } catch (FileFormatException e) {
                if (firstException == null) {
                    firstException = e;
                }
            }
        }
        throw firstException != null ? firstException : new FileFormatException("No timestamp header line before <SB>");
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
            throw new FileFormatException("Could not find any digits in header");
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
    private List<Hl7File> processLogFile(String logFile, URI destination, String workflowId, String activityId) throws IOException {
        Path logFilePath = Paths.get(logFile);

        List<Hl7File> results = new ArrayList<>();
        try (BufferedReader reader = Files.newBufferedReader(logFilePath, StandardCharsets.ISO_8859_1)) {
            List<String> splitContent = new ArrayList<>();
            String line;
            int hl7Count = 0;

            while ((line = reader.readLine()) != null) {
                // If line is empty, we're at the end of the current HL7 content
                if (line.isEmpty()) {
                    if (!splitContent.isEmpty()) {
                        results.add(transformToHl7AndUpload(logFile, splitContent, destination, hl7Count++, workflowId, activityId));
                        splitContent.clear();
                    }
                } else {
                    splitContent.add(line);
                }
            }

            // Transform last chunk of content
            if (!splitContent.isEmpty()) {
                results.add(transformToHl7AndUpload(logFile, splitContent, destination, hl7Count, workflowId, activityId));
            }
        }

        return results;
    }

    /**
     * Transform split log content into HL7, upload to S3, and add S3 path to list upon success. Log failures so as not to fail the whole operation
     *
     * @param logFile       Source HL7 log file
     * @param lines         Content of the split log
     * @param destination   Base output location
     * @param segmentNumber This HL7 file's index within parent log for error reporting
     * @return An object containing the file path if the operation was successful, or an error message if not
     */
    private Hl7File transformToHl7AndUpload(String logFile, List<String> lines, URI destination, int segmentNumber, String workflowId, String activityId) {
        try {
            return transformAndUpload(logFile, lines, destination, segmentNumber);
        } catch (IOException e) {
            logger.error("WorkflowId {} ActivityId {} - Could not write segment {} to HL7 file", workflowId, activityId, segmentNumber, e);
            return Hl7File.error(logFile, segmentNumber, "Could not write segment to HL7 file: " + e.getMessage(), workflowId, activityId);
        }
    }

    /**
     * Transform split log content into HL7 and upload to S3
     *
     * @param logFile     Source HL7 log file
     * @param lines       Content of the split log
     * @param destination Base output location
     * @return An object containing the file path if the operation was successful, or an error message if not
     * @throws IOException If an I/O error occurs
     */
    private Hl7File transformAndUpload(String logFile, List<String> lines, URI destination, int segmentNumber)
        throws IOException {
        ActivityInfo activityInfo = Activity.getExecutionContext().getInfo();

        // Need at least 3 lines (timestamp line, <SB>, and content)
        if (lines.size() < 3) {
            logger.warn("WorkflowId {} ActivityId {} - Segment {} is too short", activityInfo.getWorkflowId(), activityInfo.getActivityId(), segmentNumber);
            return Hl7File.error(logFile, segmentNumber, "Split content has fewer than 3 lines", activityInfo.getWorkflowId(), activityInfo.getActivityId());
        }

        int sbTagIndex;
        String timestamp;
        try {
            // Find the start of the HL7 content. We are assuming this is after some number of timestamped lines and then one <SB> tag
            sbTagIndex = IntStream.range(0, lines.size())
                .filter(i -> lines.get(i).contains("<SB>"))
                .findFirst()
                .orElseThrow(() -> new FileFormatException("No <SB> tag"));

            // Extract timestamp from "header" lines
            timestamp = extractTimestamp(lines, sbTagIndex);
        } catch (FileFormatException e) {
            logger.warn("WorkflowId {} ActivityId {} - Segment {} unable to parse: {}", activityInfo.getWorkflowId(), activityInfo.getActivityId(),
                segmentNumber, e.getMessage());
            return Hl7File.error(logFile, segmentNumber, "Unable to parse segment: " + e.getMessage(), activityInfo.getWorkflowId(),
                activityInfo.getActivityId());
        }

        // Define output path
        String relativePath = getTimestampPath(timestamp).resolve(timestamp + ".hl7").toString();
        logger.info("WorkflowId {} ActivityId {} - Transforming segment {} HL7 file {}", activityInfo.getWorkflowId(), activityInfo.getActivityId(),
            segmentNumber, relativePath);

        try (ByteArrayOutputStream outputStream = new ByteArrayOutputStream()) {
            for (int i = sbTagIndex + 1; i < lines.size(); i++) {
                String processed = lines.get(i).replaceAll("<R>$", "");
                // Write line with carriage return (HL7 requirement)
                outputStream.write(processed.getBytes(StandardCharsets.UTF_8));
                outputStream.write('\r');
            }

            logger.info("WorkflowId {} ActivityId {} - Uploading segment {} HL7 file {}/{}", activityInfo.getWorkflowId(), activityInfo.getActivityId(),
                segmentNumber, destination, relativePath);

            try {
                // Convert to byte array and upload to S3
                // We could use piped streams to avoid loading the whole thing into memory, but this adds complexity that isn't warranted for these small files
                String outputPath = fileHandler.putWithRetry(outputStream.toByteArray(), relativePath, destination);
                return Hl7File.success(logFile, segmentNumber, outputPath, activityInfo.getWorkflowId(), activityInfo.getActivityId());
            } catch (IOException e) {
                logger.error("WorkflowId {} ActivityId {} - Failed to upload segment {} HL7 file {}/{}",
                    activityInfo.getWorkflowId(), activityInfo.getActivityId(), segmentNumber, destination, relativePath, e);
                return Hl7File.error(logFile, segmentNumber, "Failed to upload HL7 file", activityInfo.getWorkflowId(), activityInfo.getActivityId());
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
