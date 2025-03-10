package edu.washu.tag.temporal.util;

import edu.washu.tag.temporal.exception.FileFormatException;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.stream.Collectors;

/**
 * Utility class for transforming log files into HL7 formatted files. This class processes log files by extracting timestamps, creating appropriate directory
 * structures, and formatting the content according to HL7 standards.
 */
public class HL7LogTransformer {

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
     * Transforms a log file into an HL7 formatted file.
     *
     * @param sourceFile      Absolute path to the log file
     * @param outputDirectory The output directory parent path
     * @return The absolute path of the transformed HL7 file
     * @throws IOException         If file operations fail
     * @throws FileFormatException If the file format is incorrect
     */
    public static Path transformLogFile(Path sourceFile, Path outputDirectory) throws IOException, FileFormatException {
        // Validate inputs
        validateFileExists(sourceFile);

        // Extract timestamp from file header
        String timestamp = extractTimestamp(sourceFile);

        // Create directory structure and destination path
        Path destinationPath = createDestinationPath(timestamp, outputDirectory);

        // Process and write the content
        processAndWriteContent(sourceFile, destinationPath);

        return destinationPath;
    }

    /**
     * Verifies that the source file exists.
     *
     * @param file Path to the source file
     * @throws IllegalArgumentException If the file doesn't exist
     */
    private static void validateFileExists(Path file) {
        if (!Files.exists(file)) {
            throw new IllegalArgumentException("File not found: " + file);
        }
    }

    /**
     * Extracts a timestamp from the file header.
     *
     * @param file Path to the source file
     * @return The extracted timestamp
     * @throws IOException         If reading the file fails
     * @throws FileFormatException If timestamp extraction fails
     */
    private static String extractTimestamp(Path file) throws IOException, FileFormatException {
        byte[] header = new byte[HEADER_LENGTH];

        try (var inputStream = Files.newInputStream(file)) {
            int bytesRead = inputStream.read(header, 0, header.length);

            if (bytesRead < 0) {
                throw new FileFormatException("File is empty: " + file);
            }

            if (bytesRead < header.length) {
                throw new FileFormatException(
                    String.format("File is too short, expected at least %d bytes but got %d", header.length, bytesRead)
                );
            }
        }

        String headerStr = new String(header, StandardCharsets.ISO_8859_1);
        return parseAndValidateTimestamp(headerStr, file.toString());
    }

    /**
     * Parses a timestamp from the header string.
     *
     * @param headerStr Header content as a string
     * @param filePath  Path to the source file (for error reporting)
     * @return The parsed timestamp
     * @throws FileFormatException If the timestamp cannot be parsed or is invalid
     */
    private static String parseAndValidateTimestamp(String headerStr, String filePath) throws FileFormatException {
        // Remove all non-digit characters (equivalent to tr -C -d \[:digit:\])
        String digitsOnly = headerStr.replaceAll("\\D", "");

        if (digitsOnly.isEmpty()) {
            throw new FileFormatException("Could not find any digits in header from " + filePath);
        }

        // Validate timestamp length
        if (digitsOnly.length() < MIN_TIMESTAMP_LENGTH) {
            throw new FileFormatException(
                String.format("Timestamp \"%s\" from %s is not long enough. Minimum length %d, expected length %d.",
                    digitsOnly, filePath, MIN_TIMESTAMP_LENGTH, EXPECTED_TIMESTAMP_LENGTH)
            );
        }

        return digitsOnly;
    }

    /**
     * Creates the destination directory structure and file path.
     *
     * @param timestamp       The extracted timestamp
     * @param outputDirectory Base output directory
     * @return Path to the destination file
     * @throws IOException If directory creation fails
     */
    private static Path createDestinationPath(String timestamp, Path outputDirectory) throws IOException {
        // Extract timestamp components
        String year = timestamp.substring(YEAR_START, YEAR_END);
        String month = timestamp.substring(MONTH_START, MONTH_END);
        String day = timestamp.substring(DAY_START, DAY_END);
        String hour = timestamp.substring(HOUR_START, HOUR_END);

        // Create directory structure: year/month/day/hour
        Path directory = outputDirectory.resolve(Path.of(year, month, day, hour));
        Files.createDirectories(directory);

        return directory.resolve(timestamp + ".hl7");
    }

    /**
     * Processes the file content and writes it to the destination file.
     *
     * @param sourceFile      Path to the source file
     * @param destinationPath Path to the destination file
     * @throws IOException         If file operations fail
     * @throws FileFormatException If the file format is incorrect
     */
    private static void processAndWriteContent(Path sourceFile, Path destinationPath) throws IOException, FileFormatException {
        // Read the file content
        List<String> lines;
        try {
            lines = Files.readAllLines(sourceFile, StandardCharsets.ISO_8859_1);
        } catch (IOException e) {
            throw new IOException("Failed to read source file " + sourceFile, e);
        }

        // Validate line count
        if (lines.size() < 3) {
            throw new FileFormatException("Source file invalid (less than 3 lines)  " + sourceFile);
        }

        // Skip first two lines (header) and remove last line
        lines = lines.subList(2, lines.size() - 1);

        // Process the content
        String processedContent = transformContent(lines);

        // Write to destination file
        try {
            Files.writeString(destinationPath, processedContent, StandardCharsets.UTF_8);
        } catch (IOException e) {
            throw new IOException("Failed to write destination file " + destinationPath, e);
        }
    }

    /**
     * Transforms the content lines according to HL7 format requirements.
     *
     * @param lines The content lines to transform
     * @return The transformed content
     */
    private static String transformContent(List<String> lines) {
        // 1. Remove <R> at the end of lines
        // 2. Join with carriage returns (HL7 requirement)
        return lines.stream()
            .map(line -> line.replaceAll("<R>$", ""))
            .collect(Collectors.joining("\r"));
    }
}