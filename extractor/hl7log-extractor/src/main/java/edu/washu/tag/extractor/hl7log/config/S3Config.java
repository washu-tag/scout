package edu.washu.tag.extractor.hl7log.config;

import java.net.URI;
import java.time.Duration;
import org.apache.commons.lang3.ObjectUtils;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import software.amazon.awssdk.auth.credentials.DefaultCredentialsProvider;
import software.amazon.awssdk.http.apache.ApacheHttpClient;
import software.amazon.awssdk.regions.Region;
import software.amazon.awssdk.services.s3.S3Client;
import software.amazon.awssdk.services.s3.S3ClientBuilder;
import software.amazon.awssdk.services.s3.S3Configuration;

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
        return builder.build();
    }
}
