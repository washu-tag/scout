package edu.washu.tag.temporal.util;

import io.temporal.activity.Activity;
import io.temporal.activity.ActivityInfo;
import io.temporal.workflow.Workflow;
import org.slf4j.Logger;
import org.springframework.stereotype.Component;
import software.amazon.awssdk.services.s3.S3Client;

import java.io.IOException;
import java.net.URI;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;

@Component
public class FileHandlerImpl implements FileHandler {
    private static final Logger logger = Workflow.getLogger(FileHandlerImpl.class);

    private final static String S3 = "s3";
    private final S3Client s3Client;

    public FileHandlerImpl(S3Client s3Client) {
        this.s3Client = s3Client;
    }

    @Override
    public String put(Path filePath, Path filePathsRoot, URI destination) throws IOException {
        ActivityInfo activityInfo = Activity.getExecutionContext().getInfo();
        logger.debug("WorkflowId {} ActivityId {} - Put called: filePath {} filePathsRoot {} destination {}", activityInfo.getWorkflowId(), activityInfo.getActivityId(), filePath, filePathsRoot, destination);
        Path relativeFilePath;
        Path absoluteFilePath;
        if (filePath.isAbsolute()) {
            absoluteFilePath = filePath;
            relativeFilePath = filePathsRoot.relativize(absoluteFilePath);
        } else {
            relativeFilePath = filePath;
            absoluteFilePath = filePathsRoot.resolve(relativeFilePath);
        }
        if (S3.equals(destination.getScheme())) {
            // Upload to S3
            String bucket = destination.getHost();
            String key = destination.getPath() + "/" + relativeFilePath;
            logger.info("WorkflowId {} ActivityId {} - Uploading file {} to S3 bucket {} key {}", activityInfo.getWorkflowId(), activityInfo.getActivityId(), absoluteFilePath, bucket, key);
            s3Client.putObject(builder -> builder.bucket(bucket).key(key), absoluteFilePath);
        } else {
            // Copy local files
            Path absDestination = Path.of(destination).resolve(relativeFilePath);
            absDestination.toFile().mkdirs();
            logger.info("WorkflowId {} ActivityId {} - Copying file {} to {}", activityInfo.getWorkflowId(), activityInfo.getActivityId(), absoluteFilePath, absDestination);
            Files.copy(absoluteFilePath, absDestination);
        }
        return relativeFilePath.toString();
    }

    /**
     * Put files to destination
     * If destination is S3, upload files to S3
     * If destination is local, copy files to destination
     * @param filePaths Absolute or relative paths of files to put
     * @param filePathsRoot Local root directory of file paths.
     *                      Will either be used to make relative paths absolute
     *                      or to make absolute paths relative, as we want both.
     * @param destination URI of destination
     * @return list of destination file relative paths
     * @throws IOException
     */
    @Override
    public List<String> put(List<Path> filePaths, Path filePathsRoot, URI destination) throws IOException {
        List<String> destFiles = new ArrayList<>();
        for (Path filePath : filePaths) {
            destFiles.add(put(filePath, filePathsRoot, destination));
        }
        return destFiles;
    }

    @Override
    public Path get(URI source, Path destination) throws IOException {
        ActivityInfo activityInfo = Activity.getExecutionContext().getInfo();
        if (S3.equals(source.getScheme())) {
            // Download from S3
            String bucket = source.getHost();
            String key = source.getPath();
            if (destination.toFile().isDirectory()) {
                destination = destination.resolve(Path.of(key).getFileName());
            }
            logger.debug("WorkflowId {} ActivityId {} - Downloading file from S3 bucket {} key {} to {}", activityInfo.getWorkflowId(), activityInfo.getActivityId(), bucket, key, destination);
            s3Client.getObject(builder -> builder.bucket(bucket).key(key), destination);
        } else {
            // Copy local files
            Path sourcePath = Path.of(source);
            if (destination.toFile().isDirectory()) {
                destination = destination.resolve(sourcePath.getFileName());
            }
            logger.debug("WorkflowId {} ActivityId {} - Copying file {} to {}", activityInfo.getWorkflowId(), activityInfo.getActivityId(), sourcePath, destination);
            Files.copy(sourcePath, destination);
        }
        return destination;
    }

    @Override
    public void deleteDir(Path dir) throws IOException {
        Files.walk(dir)
            .sorted(Comparator.reverseOrder()) // Sort in reverse order to delete files before directories
            .forEach(path -> {
                try {
                    Files.delete(path);
                } catch (IOException ignored) {
                    // ignored???
                }
            });
    }
}
