package edu.washu.tag.keycloak.events;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.time.Instant;
import org.jboss.logging.Logger;

/**
 * Uploads bundle bytes to a MinIO bucket via S3 PUT with Sig V4 signing.
 * Configuration is captured once at construction (endpoint URL, bucket,
 * object key, credentials, region) so each upload call only takes the
 * body to send.
 */
final class MinioBundleUploader {

    private static final Logger log = Logger.getLogger(MinioBundleUploader.class);

    private final HttpClient httpClient;
    private final URI endpoint;
    private final String bucket;
    private final String objectKey;
    private final String accessKey;
    private final String secretKey;
    private final String region;

    MinioBundleUploader(HttpClient httpClient, URI endpoint, String bucket,
                        String objectKey, String accessKey, String secretKey, String region) {
        this.httpClient = httpClient;
        this.endpoint = endpoint;
        this.bucket = bucket;
        this.objectKey = objectKey;
        this.accessKey = accessKey;
        this.secretKey = secretKey;
        this.region = region;
    }

    /**
     * Upload bundle bytes. Returns true on 2xx, false otherwise. Failures
     * are logged but not raised — the caller (factory's scheduled writer)
     * handles retry policy.
     */
    boolean upload(byte[] body) {
        String canonicalPath = "/" + bucket + "/" + objectKey;
        // Compute the Host header value the same way the JDK HttpClient
        // will: scheme-aware host:port for non-default ports. AwsSigV4
        // signs THIS string as the host header; the request itself
        // can't set Host explicitly — java.net.http rejects it as a
        // restricted header — but the client emits the same value
        // automatically from the URI, so the signature validates.
        String host = endpoint.getHost() + (endpoint.getPort() > 0 ? ":" + endpoint.getPort() : "");

        AwsSigV4.Headers headers = AwsSigV4.signPut(
                accessKey, secretKey, region, host, canonicalPath, body, Instant.now());

        URI requestUri = endpoint.resolve(canonicalPath);
        HttpRequest req = HttpRequest.newBuilder()
                .uri(requestUri)
                .timeout(Duration.ofSeconds(10))
                .header("x-amz-content-sha256", headers.contentSha256())
                .header("x-amz-date", headers.amzDate())
                .header("Authorization", headers.authorization())
                .header("Content-Type", "application/gzip")
                .PUT(HttpRequest.BodyPublishers.ofByteArray(body))
                .build();

        try {
            HttpResponse<String> resp = httpClient.send(req, HttpResponse.BodyHandlers.ofString());
            if (resp.statusCode() >= 200 && resp.statusCode() < 300) {
                log.infof("Published OPA bundle to %s (%d bytes)", requestUri, body.length);
                return true;
            }
            log.warnf("OPA bundle upload to %s returned %d: %s",
                    requestUri, resp.statusCode(), resp.body());
            return false;
        } catch (Exception e) {
            log.errorf("OPA bundle upload to %s failed: %s", requestUri, e.getMessage());
            return false;
        }
    }
}
