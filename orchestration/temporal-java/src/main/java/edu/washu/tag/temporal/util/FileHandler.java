package edu.washu.tag.temporal.util;

import java.io.IOException;
import java.net.URI;
import java.net.URISyntaxException;
import java.nio.file.Path;
import java.util.List;

public interface FileHandler {
    String put(Path filePath, Path filePathsRoot, URI destination) throws IOException, URISyntaxException;
    List<String> put(List<Path> filePaths, Path filePathsRoot, URI destination) throws IOException, URISyntaxException;
    void deleteDir(Path dir) throws IOException;
}
