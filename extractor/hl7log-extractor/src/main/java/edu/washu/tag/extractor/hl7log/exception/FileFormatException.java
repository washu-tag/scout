package edu.washu.tag.extractor.hl7log.exception;

/**
 * Exception thrown when a file format is invalid or does not conform to the expected structure.
 */
public class FileFormatException extends Exception {
    /**
     * Constructs a new FileFormatException with the specified detail message.
     *
     * @param message the detail message explaining the reason for the exception
     */
    public FileFormatException(String message) {
        super(message);
    }
}
