package edu.washu.tag.temporal.util;

import java.io.IOException;
import java.net.URI;
import java.nio.file.Path;
import java.util.List;

public interface FileHandler {
    String put(Path filePath, Path filePathsRoot, URI destination) throws IOException;
    List<String> put(List<Path> filePaths, Path filePathsRoot, URI destination) throws IOException;
    Path get(URI source, Path destination) throws IOException;
    void deleteDir(Path dir) throws IOException;
}
