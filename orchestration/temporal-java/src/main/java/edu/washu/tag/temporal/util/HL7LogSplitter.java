package edu.washu.tag.temporal.util;

import java.io.*;
import java.nio.file.*;
import java.util.*;

/**
 * Utility class for splitting HL7 log files. Splits a log file into multiple smaller files at empty lines which likely represent message boundaries in HL7
 * logs.
 */
public class HL7LogSplitter {

    /**
     * Splits an HL7 log file into multiple files at empty line boundaries.
     *
     * @param logFile         The HL7 log file to split
     * @param outputDirectory The output directory
     * @return List of generated output file paths (absolute)
     * @throws IOException              If an I/O error occurs
     * @throws IllegalArgumentException If the input file doesn't exist
     */
    public static List<Path> splitLogFile(String logFile, Path outputDirectory) throws IOException {
        Path logFilePath = Paths.get(logFile);

        // Check if file exists
        if (!Files.exists(logFilePath) || !Files.isRegularFile(logFilePath)) {
            throw new IllegalArgumentException("File not found: " + logFilePath);
        }

        // Get file name without directory path
        String fileName = logFilePath.getFileName().toString();

        // Get prefix (name without extension)
        String prefix = fileName.contains(".") ?
            fileName.substring(0, fileName.lastIndexOf('.')) :
            fileName;

        return splitLogFile(logFilePath, prefix, outputDirectory);
    }

    private static List<Path> splitLogFile(Path logFilePath, String outputPrefix, Path outputDirectory)
        throws IOException {
        List<Path> outputFiles = new ArrayList<>();
        try (BufferedReader reader = Files.newBufferedReader(logFilePath)) {
            StringBuilder currentContent = new StringBuilder();
            String line;
            int fileCounter = 0;

            while ((line = reader.readLine()) != null) {
                // Check if line is empty (equivalent to only carriage return)
                if (line.isEmpty()) {
                    // Save current content if not empty
                    if (!currentContent.isEmpty()) {
                        String outputFileName = String.format("%s.%05d", outputPrefix, fileCounter++);
                        Path outputPath = outputDirectory.resolve(outputFileName);
                        Files.writeString(outputPath, currentContent.toString());
                        outputFiles.add(outputPath);
                        currentContent.setLength(0); // Clear buffer
                    }
                } else {
                    // Add line to current content
                    currentContent.append(line).append(System.lineSeparator());
                }
            }

            // Save last content if there's anything left
            if (!currentContent.isEmpty()) {
                String outputFileName = String.format("%s.%05d", outputPrefix, fileCounter);
                Path outputPath = outputDirectory.resolve(outputFileName);
                Files.writeString(outputPath, currentContent.toString());
                outputFiles.add(outputPath);
            }
        }

        return outputFiles;
    }
}
