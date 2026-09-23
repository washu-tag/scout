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
import software.amazon.awssdk.services.s3.model.ServerSideEncryption;

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
final class MinioBundleUploader implements AutoCloseable {

    private static final Logger log = Logger.getLogger(MinioBundleUploader.class);

    private final S3Client s3;
    private final String bucket;
    private final String objectKey;

    /**
     * AWS with sseType S3 only. An SCP can deny s3:PutObject without an
     * x-amz-server-side-encryption header, and a bucket default does not satisfy
     * it; but an explicit header also overrides an SSE-KMS bucket default, so it
     * is opt-in. Never set for MinIO, where SSE-S3 depends on tenant config.
     */
    private final boolean requestSse;

    MinioBundleUploader(URI endpoint, String bucket, String objectKey,
                        String accessKey, String secretKey, String region, String sseType) {
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
        this.requestSse = requestSse(endpoint, sseType);
    }

    // Package-private test seam: inject a (mock) S3Client so the
    // upload() success/failure -> boolean contract can be exercised without a
    // live S3 endpoint. Mirrors the factory's enableForTest seam.
    MinioBundleUploader(S3Client s3, String bucket, String objectKey) {
        this(s3, bucket, objectKey, false);
    }

    // As above, with the SSE header forced on or off so both paths are testable.
    MinioBundleUploader(S3Client s3, String bucket, String objectKey, boolean requestSse) {
        this.s3 = s3;
        this.bucket = bucket;
        this.objectKey = objectKey;
        this.requestSse = requestSse;
    }

    /** SSE-S3 header only for real AWS S3 and sseType S3 (case-insensitive). */
    static boolean requestSse(URI endpoint, String sseType) {
        if (sseType != null && !sseType.isBlank() && !sseType.equalsIgnoreCase("S3")
                && !sseType.equalsIgnoreCase("NONE")) {
            log.warnf("Unknown OPA bundle SSE type '%s' (expected S3 or NONE); sending no SSE header",
                    sseType);
        }
        return isAwsS3(endpoint) && "S3".equalsIgnoreCase(sseType);
    }

    /** Real AWS S3: no endpoint override, or an explicit *.amazonaws.com one. */
    private static boolean isAwsS3(URI endpoint) {
        if (endpoint == null) {
            return true;
        }
        String host = endpoint.getHost();
        return host != null && host.endsWith(".amazonaws.com");
    }

    /**
     * Upload bundle bytes. Returns true on success, false on failure.
     * Failures are logged; the caller (factory's scheduled publisher)
     * handles retry policy.
     */
    boolean upload(byte[] body) {
        try {
            PutObjectRequest.Builder req = PutObjectRequest.builder()
                    .bucket(bucket)
                    .key(objectKey)
                    .contentType("application/gzip");
            if (requestSse) {
                req.serverSideEncryption(ServerSideEncryption.AES256);
            }
            s3.putObject(req.build(), RequestBody.fromBytes(body));
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

    /**
     * Closes the underlying {@link S3Client}, releasing the SDK's HTTP
     * connection pool and any other AWS-SDK-internal resources. Safe to
     * call multiple times — {@code S3Client.close()} is idempotent.
     */
    @Override
    public void close() {
        s3.close();
    }
}
