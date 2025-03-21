package edu.washu.tag.temporal.util;

import io.temporal.activity.Activity;
import io.temporal.activity.ActivityInfo;
import io.temporal.failure.ApplicationFailure;
import io.temporal.workflow.Workflow;
import java.io.IOException;
import java.net.URI;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.stream.Stream;
import org.slf4j.Logger;
import org.springframework.retry.RetryContext;
import org.springframework.retry.support.RetrySynchronizationManager;
import org.springframework.stereotype.Component;
import software.amazon.awssdk.core.sync.RequestBody;
import software.amazon.awssdk.services.s3.S3Client;
import software.amazon.awssdk.services.s3.model.ObjectIdentifier;
import software.amazon.awssdk.services.s3.model.S3Object;

@Component
public class FileHandlerImpl implements FileHandler {

    private static final Logger logger = Workflow.getLogger(FileHandlerImpl.class);

    private static final String S3 = "s3";
    private final S3Client s3Client;

    public FileHandlerImpl(S3Client s3Client) {
        this.s3Client = s3Client;
    }

    @Override
    public String putWithRetry(Path filePath, Path filePathsRoot, URI destination) throws IOException {
        retryLogging();
        return put(filePath, filePathsRoot, destination);
    }

    @Override
    public String putWithRetry(byte[] data, String relativePath, URI destination) {
        URI destinationWithRelativePath = URI.create(destination + "/" + relativePath);
        return putWithRetry(data, destinationWithRelativePath);
    }

    @Override
    public String putWithRetry(byte[] data, URI destination) {
        retryLogging();
        ActivityInfo activityInfo = Activity.getExecutionContext().getInfo();
        if (!S3.equals(destination.getScheme())) {
            throw new UnsupportedOperationException("Unsupported destination scheme " + destination.getScheme());
        }
        String bucket = destination.getHost();
        String key = destination.getPath();
        logger.debug("WorkflowId {} ActivityId {} - Uploading bytes to S3 bucket {} key {}", activityInfo.getWorkflowId(), activityInfo.getActivityId(),
            bucket, key);
        s3Client.putObject(builder -> builder.bucket(bucket).key(key), RequestBody.fromBytes(data));
        return destination.toString();
    }

    private void retryLogging() {
        if (logger.isDebugEnabled()) {
            RetryContext context = RetrySynchronizationManager.getContext();
            if (context != null) {
                logger.debug("Retry number {}", context.getRetryCount());
            }
        }
    }

    @Override
    public String put(Path filePath, Path filePathsRoot, URI destination) throws IOException {
        ActivityInfo activityInfo = Activity.getExecutionContext().getInfo();
        logger.debug("WorkflowId {} ActivityId {} - Put called: filePath {} filePathsRoot {} destination {}", activityInfo.getWorkflowId(),
            activityInfo.getActivityId(), filePath, filePathsRoot, destination);
        Path relativeFilePath;
        Path absoluteFilePath;
        if (filePath.isAbsolute()) {
            absoluteFilePath = filePath;
            relativeFilePath = filePathsRoot.relativize(absoluteFilePath);
        } else {
            relativeFilePath = filePath;
            absoluteFilePath = filePathsRoot.resolve(relativeFilePath);
        }

        String outputPath;
        if (S3.equals(destination.getScheme())) {
            // Upload to S3
            String bucket = destination.getHost();
            String key = destination.getPath() + "/" + relativeFilePath;
            logger.debug("Uploading file {} to S3 bucket {} key {}", absoluteFilePath, bucket, key);
            s3Client.putObject(builder -> builder.bucket(bucket).key(key), absoluteFilePath);
            outputPath = destination + "/" + relativeFilePath; // URI#resolve strips trailing path from destination;
        } else {
            // Copy local files
            Path absDestination = Path.of(destination).resolve(relativeFilePath);
            Files.createDirectories(absDestination);
            logger.debug("Copying file {} to {}", absoluteFilePath, absDestination);
            Files.copy(absoluteFilePath, absDestination);
            outputPath = absoluteFilePath.toString();
        }
        return outputPath;
    }

    /**
     * Put files to destination. If destination is S3, upload files to S3 If destination is local, copy files to destination
     *
     * @param filePaths     Absolute or relative paths of files to put
     * @param filePathsRoot Local root directory of file paths. Will either be used to make relative paths absolute or to make absolute paths relative, as we
     *                      want both.
     * @param destination   URI of destination
     * @return list of destination file absolute paths
     * @throws IOException if an I/O error occurs
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
            logger.debug("WorkflowId {} ActivityId {} - Downloading file from S3 bucket {} key {} to {}", activityInfo.getWorkflowId(),
                activityInfo.getActivityId(), bucket, key, destination);
            s3Client.getObject(builder -> builder.bucket(bucket).key(key), destination);
        } else {
            // Copy local files
            Path sourcePath = Path.of(source);
            if (destination.toFile().isDirectory()) {
                destination = destination.resolve(sourcePath.getFileName());
            }
            logger.debug("WorkflowId {} ActivityId {} - Copying file {} to {}", activityInfo.getWorkflowId(), activityInfo.getActivityId(), sourcePath,
                destination);
            Files.copy(sourcePath, destination);
        }
        return destination;
    }

    @Override
    public byte[] read(URI source) throws IOException {
        retryLogging();
        ActivityInfo activityInfo = Activity.getExecutionContext().getInfo();
        if (S3.equals(source.getScheme())) {
            String bucket = source.getHost();
            String key = source.getPath();
            logger.debug("WorkflowId {} ActivityId {} - Reading file from S3 bucket {} key {}", activityInfo.getWorkflowId(),
                activityInfo.getActivityId(), bucket, key);

            return s3Client.getObjectAsBytes(builder -> builder.bucket(bucket).key(key)).asByteArray();
        } else {
            return Files.readAllBytes(Path.of(source));
        }
    }

    @Override
    public void deleteMultiple(List<URI> uris) {
        String scheme = uris.getFirst().getScheme();
        if (!S3.equals(scheme)) {
            throw new UnsupportedOperationException("Unsupported destination scheme " + scheme);
        }
        String bucket = uris.getFirst().getHost();
        List<ObjectIdentifier> keys = uris.stream()
            .map(URI::getPath)
            .map(key -> ObjectIdentifier.builder().key(key).build())
            .toList();
        logger.debug("Deleting {} keys from S3 bucket {}", keys.size(), bucket);
        s3Client.deleteObjects(builder -> builder.bucket(bucket).delete(deleteBuilder -> deleteBuilder.objects(keys)));
    }

    @Override
    public List<String> ls(URI source) {
        String scheme = source.getScheme();
        if (!S3.equals(scheme)) {
            throw new UnsupportedOperationException("Unsupported destination scheme " + scheme);
        }
        String bucket = source.getHost();
        String prefix = source.getPath();
        logger.debug("Listing files in S3 bucket {} with prefix {}", bucket, prefix);
        try (Stream<S3Object> paths = s3Client.listObjectsV2Paginator(builder -> builder.bucket(bucket).prefix(prefix))
            .contents()
            .stream()) {
            return paths
                .map(s3Object -> "s3://" + bucket + "/" + s3Object.key())
                .toList();
        } catch (Exception e) {
            throw ApplicationFailure.newFailureWithCause("Error listing files in S3", "type", e);
        }
    }
}
