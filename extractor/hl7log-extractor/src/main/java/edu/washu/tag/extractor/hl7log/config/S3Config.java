package edu.washu.tag.extractor.hl7log.config;

import java.net.URI;
import java.time.Duration;
import org.apache.commons.lang3.ObjectUtils;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import software.amazon.awssdk.auth.credentials.DefaultCredentialsProvider;
import software.amazon.awssdk.core.SdkRequest;
import software.amazon.awssdk.core.interceptor.Context;
import software.amazon.awssdk.core.interceptor.ExecutionAttributes;
import software.amazon.awssdk.core.interceptor.ExecutionInterceptor;
import software.amazon.awssdk.http.apache.ApacheHttpClient;
import software.amazon.awssdk.regions.Region;
import software.amazon.awssdk.services.s3.S3Client;
import software.amazon.awssdk.services.s3.S3ClientBuilder;
import software.amazon.awssdk.services.s3.S3Configuration;
import software.amazon.awssdk.services.s3.model.CreateMultipartUploadRequest;
import software.amazon.awssdk.services.s3.model.PutObjectRequest;
import software.amazon.awssdk.services.s3.model.ServerSideEncryption;

/**
 * Configuration class for setting up the S3 client with custom endpoint.
 * Uses Apache HTTP client for better performance and connection management.
 */
@Configuration
public class S3Config {

    @Value("${s3.endpoint:#{null}}")
    private String endpoint;

    @Value("${s3.region}")
    private String region;

    @Value("${s3.max-connections}")
    private Integer maxConnections;

    @Value("${s3.path-style-access:false}")
    private boolean pathStyleAccess;

    /** S3 requests SSE-S3 on uploads; NONE (default) leaves the bucket default to apply. */
    @Value("${s3.sse-type:NONE}")
    private String sseType;

    /**
     * Creates an S3 client bean. Endpoint and path-style access are configured independently
     * so the same code targets real AWS S3 (no overrides) or an S3-compatible service like MinIO
     * (endpoint URL + path-style access).
     *
     * @return Configured S3Client instance.
     */
    @Bean
    public S3Client s3Client() {
        S3ClientBuilder builder = S3Client.builder()
                .region(Region.of(region))
                .credentialsProvider(DefaultCredentialsProvider.create())
                .serviceConfiguration(S3Configuration.builder()
                        .pathStyleAccessEnabled(pathStyleAccess)
                        .build())
                .httpClientBuilder(ApacheHttpClient.builder()
                        .maxConnections(ObjectUtils.defaultIfNull(maxConnections, 50))
                        .connectionTimeout(Duration.ofSeconds(5)));
        if (endpoint != null && !endpoint.isBlank()) {
            builder = builder.endpointOverride(URI.create(endpoint));
        }
        boolean sse = requestsSse(sseType);  // validate on every endpoint, not just AWS
        if (isAwsS3(endpoint) && sse) {
            builder = builder.overrideConfiguration(
                    config -> config.addExecutionInterceptor(new SseS3Interceptor()));
        }
        return builder.build();
    }

    /** Real AWS S3: no endpoint override, or an explicit *.amazonaws.com one. */
    static boolean isAwsS3(String endpoint) {
        if (endpoint == null || endpoint.isBlank()) {
            return true;
        }
        String host = URI.create(endpoint).getHost();
        return host != null && host.endsWith(".amazonaws.com");
    }

    /**
     * Parses s3.sse-type. An explicit header overrides a bucket's SSE-KMS default, so it is
     * opt-in for sites whose SCP or bucket policy requires one.
     */
    static boolean requestsSse(String sseType) {
        if (sseType == null || sseType.isBlank() || sseType.equalsIgnoreCase("NONE")) {
            return false;
        }
        if (sseType.equalsIgnoreCase("S3")) {
            return true;
        }
        throw new IllegalArgumentException("s3.sse-type must be S3 or NONE, got: " + sseType);
    }

    /**
     * Requests SSE-S3 on uploads. An SCP or bucket policy can deny s3:PutObject without an
     * x-amz-server-side-encryption header, and a bucket default does not satisfy it because the
     * condition inspects the request. Registered only for real AWS S3 with s3.sse-type=S3; on
     * S3-compatible services such as MinIO, server-side encryption depends on tenant
     * configuration.
     */
    static final class SseS3Interceptor implements ExecutionInterceptor {

        @Override
        public SdkRequest modifyRequest(Context.ModifyRequest context,
                ExecutionAttributes executionAttributes) {
            SdkRequest request = context.request();
            if (request instanceof PutObjectRequest put && put.serverSideEncryption() == null) {
                return put.toBuilder().serverSideEncryption(ServerSideEncryption.AES256).build();
            }
            // Unreachable with a sync client, but a future transfer manager or
            // multipart threshold would otherwise start the upload bare.
            if (request instanceof CreateMultipartUploadRequest create
                    && create.serverSideEncryption() == null) {
                return create.toBuilder().serverSideEncryption(ServerSideEncryption.AES256).build();
            }
            return request;
        }
    }
}
