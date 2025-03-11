package edu.washu.tag.temporal.util;

import edu.washu.tag.temporal.exception.FileFormatException;
import io.temporal.workflow.Workflow;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;
import org.slf4j.Logger;

/**
 * Utility class for processing HL7 log files: split the log file at empty line boundaries
 * and transform each section into an HL7 formatted file.
 */
public class Hl7LogProcessor {

    private static final Logger logger = Workflow.getLogger(Hl7LogProcessor.class);

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

    /**
     * Splits and transforms an HL7 log file into multiple HL7 formatted files
     *
     * @param logFile         The HL7 log file to process
     * @param outputDirectory The base output directory
     * @return List of generated output file paths (absolute)
     * @throws IOException         If an I/O error occurs
     */
    public static List<Path> processLogFile(String logFile, Path outputDirectory) throws IOException {
        Path logFilePath = Paths.get(logFile);

        // Check if file exists
        if (!Files.exists(logFilePath) || !Files.isRegularFile(logFilePath)) {
            throw new IllegalArgumentException("File not found: " + logFilePath);
        }

        List<Path> outputFiles = new ArrayList<>();
        try (BufferedReader reader = Files.newBufferedReader(logFilePath, StandardCharsets.ISO_8859_1)) {
            List<String> splitContent = new ArrayList<>();
            String line;
            int hl7Count = 0;

            while ((line = reader.readLine()) != null) {
                // If line is empty, we're at the end of the current HL7 content
                if (line.isEmpty()) {
                    if (!splitContent.isEmpty()) {
                        transformToHl7AndAddToList(outputFiles, splitContent, outputDirectory, logFilePath, hl7Count++);
                        splitContent.clear();
                    }
                } else {
                    splitContent.add(line);
                }
            }

            // Transform last chunk of content
            if (!splitContent.isEmpty()) {
                transformToHl7AndAddToList(outputFiles, splitContent, outputDirectory, logFilePath, hl7Count);
            }
        }

        return outputFiles;
    }

    private static void transformToHl7AndAddToList(List<Path> outputFiles, List<String> splitContent, Path outputDirectory, Path logFilePath, int hl7Count) {
        String sourceInfo = logFilePath + " (section " + hl7Count + ")";
        try {
            Path outputPath = transformToHl7(splitContent, outputDirectory, sourceInfo);
            outputFiles.add(outputPath);
        } catch (IOException | FileFormatException e) {
            logger.error("Unable to transform to HL7 {}", sourceInfo, e);
        }
    }

    /**
     * Transform split log content into HL7
     *
     * @param lines           Content of the split log
     * @param outputDirectory Base output directory
     * @param sourceInfo      Source information for error reporting
     * @return Path to the created output file
     * @throws IOException         If an I/O error occurs
     * @throws FileFormatException If the file format is invalid
     */
    private static Path transformToHl7(List<String> lines, Path outputDirectory, String sourceInfo)
        throws IOException, FileFormatException {

        // Need at least 3 lines (2 header lines + content)
        if (lines.size() < 3) {
            throw new FileFormatException("Split content has fewer than 3 lines: " + sourceInfo);
        }

        // Extract timestamp from the segment's first HEADER_LENGTH bytes
        String timestamp = extractTimestamp(lines, sourceInfo);

        // Create directory structure based on timestamp components
        Path destination = makeTimestampPath(outputDirectory, timestamp);
        Files.createDirectories(destination);

        // Define output file path
        Path outputPath = destination.resolve(timestamp + ".hl7");

        // Skip the first two lines and transform content
        try (BufferedWriter writer = Files.newBufferedWriter(outputPath, StandardCharsets.UTF_8)) {
            // Start from the third line (index 2)
            for (int i = 2; i < lines.size(); i++) {
                String processedLine = lines.get(i).replaceAll("<R>$", "");

                // Write line with carriage return (HL7 requirement)
                writer.write(processedLine);
                writer.write('\r');
            }
        }

        return outputPath;
    }

    private static Path makeTimestampPath(Path outputDirectory, String timestamp) {
        String year = timestamp.substring(YEAR_START, YEAR_END);
        String month = timestamp.substring(MONTH_START, MONTH_END);
        String day = timestamp.substring(DAY_START, DAY_END);
        String hour = timestamp.substring(HOUR_START, HOUR_END);

        // Create the directory path
        return outputDirectory.resolve(Path.of(year, month, day, hour));
    }

    /**
     * Extracts a timestamp from the first lines of a segment.
     *
     * @param lines        Content of split file
     * @param sourceInfo   Source information for error reporting
     * @return The extracted timestamp
     * @throws FileFormatException If timestamp extraction fails
     */
    private static String extractTimestamp(List<String> lines, String sourceInfo) throws FileFormatException {
        // Combine the first few lines to ensure we have enough bytes for the header
        StringBuilder headerBuilder = new StringBuilder();
        for (String line : lines) {
            headerBuilder.append(line);
            if (headerBuilder.length() >= HEADER_LENGTH) {
                break;
            }
            headerBuilder.append(System.lineSeparator());  // Add back the newline
        }

        String headerStr = headerBuilder.toString();

        // Check if we have enough bytes
        if (headerStr.length() < HEADER_LENGTH) {
            throw new FileFormatException(
                String.format("Segment header is too short, expected at least %d bytes but got %d",
                    HEADER_LENGTH, headerStr.length())
            );
        }

        // Extract the timestamp from the header
        return parseAndValidateTimestamp(headerStr.substring(0, HEADER_LENGTH), sourceInfo);
    }

    /**
     * Parses a timestamp from the header string.
     *
     * @param headerStr  Header content as a string
     * @param sourceInfo Source information for error reporting
     * @return The parsed timestamp
     * @throws FileFormatException If the timestamp cannot be parsed or is invalid
     */
    private static String parseAndValidateTimestamp(String headerStr, String sourceInfo) throws FileFormatException {
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
}