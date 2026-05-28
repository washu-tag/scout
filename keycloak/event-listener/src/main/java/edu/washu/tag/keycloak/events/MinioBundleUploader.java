package edu.washu.tag.keycloak.events;

import java.net.URI;
import org.jboss.logging.Logger;
import software.amazon.awssdk.auth.credentials.AwsBasicCredentials;
import software.amazon.awssdk.auth.credentials.AwsCredentialsProvider;
import software.amazon.awssdk.auth.credentials.DefaultCredentialsProvider;
import software.amazon.awssdk.auth.credentials.StaticCredentialsProvider;
import software.amazon.awssdk.core.sync.RequestBody;
import software.amazon.awssdk.http.urlconnection.UrlConnectionHttpClient;
import software.amazon.awssdk.regions.Region;
import software.amazon.awssdk.services.s3.S3Client;
import software.amazon.awssdk.services.s3.S3ClientBuilder;
import software.amazon.awssdk.services.s3.S3Configuration;
import software.amazon.awssdk.services.s3.model.PutObjectRequest;
import software.amazon.awssdk.services.s3.model.S3Exception;

/**
 * Uploads bundle bytes to an S3-compatible bucket (MinIO or AWS S3) via
 * the AWS SDK v2.
 *
 * <p>Credentials: if both {@code accessKey} and {@code secretKey} are
 * non-blank (the MinIO case with static keys provisioned by Ansible),
 * uses {@link StaticCredentialsProvider}. Otherwise uses
 * {@link DefaultCredentialsProvider}, which picks up IRSA via the
 * web-identity-token-file path when running with an EKS IAM role, falling
 * through to env vars, instance profile, etc.
 *
 * <p>Endpoint: optional. Set for MinIO (with path-style addressing); omit
 * for AWS S3 so the SDK uses the region-derived default endpoint.
 *
 * <p>The S3 client is constructed once per factory init and reused; the
 * URL-connection HTTP client is sync, which is fine because the listener
 * issues at most one PUT per debounce window.
 */
final class MinioBundleUploader {

    private static final Logger log = Logger.getLogger(MinioBundleUploader.class);

    private final S3Client s3;
    private final String bucket;
    private final String objectKey;

    MinioBundleUploader(URI endpoint, String bucket, String objectKey,
                        String accessKey, String secretKey, String region) {
        this.bucket = bucket;
        this.objectKey = objectKey;

        AwsCredentialsProvider creds = hasStaticKeys(accessKey, secretKey)
                ? StaticCredentialsProvider.create(AwsBasicCredentials.create(accessKey, secretKey))
                : DefaultCredentialsProvider.create();

        S3ClientBuilder builder = S3Client.builder()
                .credentialsProvider(creds)
                .region(Region.of(region))
                .httpClient(UrlConnectionHttpClient.create());

        if (endpoint != null) {
            // Path-style addressing is required for MinIO. We set it only
            // when an endpoint is overridden; for real S3 the SDK picks
            // virtual-host addressing by default, which is preferred there.
            builder.endpointOverride(endpoint)
                    .serviceConfiguration(S3Configuration.builder()
                            .pathStyleAccessEnabled(true)
                            .build());
        }

        this.s3 = builder.build();
    }

    /**
     * Upload bundle bytes. Returns true on success, false on failure.
     * Failures are logged; the caller (factory's scheduled publisher)
     * handles retry policy.
     */
    boolean upload(byte[] body) {
        try {
            s3.putObject(
                    PutObjectRequest.builder()
                            .bucket(bucket)
                            .key(objectKey)
                            .contentType("application/gzip")
                            .build(),
                    RequestBody.fromBytes(body));
            log.infof("Published OPA bundle to s3://%s/%s (%d bytes)",
                    bucket, objectKey, body.length);
            return true;
        } catch (S3Exception e) {
            log.warnf("OPA bundle upload to s3://%s/%s failed: HTTP %d %s",
                    bucket, objectKey, e.statusCode(),
                    e.awsErrorDetails() == null ? "" : e.awsErrorDetails().errorMessage());
            return false;
        } catch (Exception e) {
            log.errorf(e, "OPA bundle upload to s3://%s/%s failed", bucket, objectKey);
            return false;
        }
    }

    private static boolean hasStaticKeys(String accessKey, String secretKey) {
        return accessKey != null && !accessKey.isBlank()
                && secretKey != null && !secretKey.isBlank();
    }
}
