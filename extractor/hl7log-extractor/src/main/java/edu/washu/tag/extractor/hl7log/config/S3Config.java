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
import software.amazon.awssdk.services.s3.S3Configuration;

@Configuration
public class S3Config {

    @Value("${s3.endpoint}")
    private String endpoint;

    @Value("${s3.region}")
    private String region;

    @Value("${s3.max-connections}")
    private Integer maxConnections;

    @Bean
    public S3Client s3Client() {
        return S3Client.builder()
                .endpointOverride(URI.create(endpoint))
                .region(Region.of(region))
                .credentialsProvider(DefaultCredentialsProvider.create())
                .serviceConfiguration(S3Configuration.builder()
                        .pathStyleAccessEnabled(true)
                        .build())
                .httpClientBuilder(ApacheHttpClient.builder()
                        .maxConnections(ObjectUtils.defaultIfNull(maxConnections, 50))
                        .connectionTimeout(Duration.ofSeconds(5)))
                .build();
    }
}