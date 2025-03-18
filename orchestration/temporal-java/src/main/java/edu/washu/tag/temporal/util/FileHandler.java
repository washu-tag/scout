package edu.washu.tag.temporal.util;

import java.io.IOException;
import java.net.URI;
import java.nio.file.Path;
import java.util.List;
import org.springframework.retry.annotation.Backoff;
import org.springframework.retry.annotation.Retryable;

public interface FileHandler {
    @Retryable(maxAttempts = 5, backoff = @Backoff(delay = 1000, multiplier = 2))
    String putWithRetry(Path filePath, Path filePathsRoot, URI destination) throws IOException;

    @Retryable(maxAttempts = 5, backoff = @Backoff(delay = 1000, multiplier = 2))
    String putWithRetry(byte[] data, String relativePath, URI destination) throws IOException;

    @Retryable(maxAttempts = 5, backoff = @Backoff(delay = 1000, multiplier = 2))
    String putWithRetry(byte[] data, URI destination) throws IOException;

    String put(Path filePath, Path filePathsRoot, URI destination) throws IOException;

    List<String> put(List<Path> filePaths, Path filePathsRoot, URI destination) throws IOException;

    Path get(URI source, Path destination) throws IOException;

    @Retryable(maxAttempts = 5, backoff = @Backoff(delay = 1000, multiplier = 2))
    byte[] read(URI source) throws IOException;

    void deleteMultiple(List<URI> uris);
}
