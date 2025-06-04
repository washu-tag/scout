package edu.washu.tag.extractor.hl7log.util;

import java.io.IOException;
import java.net.URI;
import java.nio.file.Path;
import java.util.List;
import org.springframework.retry.annotation.Backoff;
import org.springframework.retry.annotation.Retryable;

/**
 * File handling operations interface.
 * Provides methods for uploading files, reading files, and listing directories with retry capabilities.
 */
public interface FileHandler {
    /**
     * Uploads a file to the specified destination with retry capabilities.
     *
     * @param filePath       The path of the file to upload.
     * @param filePathsRoot  The root path for resolving relative paths.
     * @param destination    The URI of the destination where the file will be uploaded.
     * @return The URI of the uploaded file.
     * @throws IOException If an I/O error occurs during the upload.
     */
    @Retryable(maxAttempts = 5, backoff = @Backoff(delay = 1000, multiplier = 2))
    String putWithRetry(Path filePath, Path filePathsRoot, URI destination) throws IOException;

    /**
     * Uploads byte data to the specified destination with retry capabilities.
     *
     * @param data          The byte array data to upload.
     * @param relativePath  The relative path for the file in the destination.
     * @param destination   The URI of the destination where the data will be uploaded.
     * @return The URI of the uploaded data.
     * @throws IOException If an I/O error occurs during the upload.
     */
    @Retryable(maxAttempts = 5, backoff = @Backoff(delay = 1000, multiplier = 2))
    String putWithRetry(byte[] data, String relativePath, URI destination) throws IOException;

    /**
     * Uploads byte data to the specified destination with retry capabilities.
     *
     * @param data        The byte array data to upload.
     * @param destination The URI of the destination where the data will be uploaded.
     * @return The URI of the uploaded data.
     * @throws IOException If an I/O error occurs during the upload.
     */
    @Retryable(maxAttempts = 5, backoff = @Backoff(delay = 1000, multiplier = 2))
    String putWithRetry(byte[] data, URI destination) throws IOException;

    /**
     * Uploads a file to the specified destination.
     *
     * @param filePath       The path of the file to upload.
     * @param filePathsRoot  The root path for resolving relative paths.
     * @param destination    The URI of the destination where the file will be uploaded.
     * @return The URI of the uploaded file.
     * @throws IOException If an I/O error occurs during the upload.
     */
    String put(Path filePath, Path filePathsRoot, URI destination) throws IOException;

    /**
     * Uploads multiple files to the specified destination.
     *
     * @param filePaths      A list of paths of the files to upload.
     * @param filePathsRoot  The root path for resolving relative paths.
     * @param destination    The URI of the destination where the files will be uploaded.
     * @return A list of URIs of the uploaded files.
     * @throws IOException If an I/O error occurs during the upload.
     */
    List<String> put(List<Path> filePaths, Path filePathsRoot, URI destination) throws IOException;

    /**
     * Downloads a file from the specified source URI to the destination path.
     *
     * @param source      The URI of the source file to download.
     * @param destination The path where the file will be downloaded.
     * @return The path of the downloaded file.
     * @throws IOException If an I/O error occurs during the download.
     */
    Path get(URI source, Path destination) throws IOException;

    /**
     * Reads the content of a file from the specified source URI.
     *
     * @param source The URI of the source file to read.
     * @return The byte array content of the file.
     * @throws IOException If an I/O error occurs during the read operation.
     */
    @Retryable(maxAttempts = 5, backoff = @Backoff(delay = 1000, multiplier = 2))
    byte[] read(URI source) throws IOException;

    /**
     * Deletes multiple files specified by their URIs.
     *
     * @param uris A list of URIs of the files to delete.
     */
    void deleteMultiple(List<URI> uris);

    /**
     * Lists the contents of a directory specified by the source URI.
     *
     * @param source The URI of the directory to list.
     * @return A list of file names in the directory.
     */
    @Retryable(maxAttempts = 5, backoff = @Backoff(delay = 1000, multiplier = 2))
    List<String> ls(URI source);
}
