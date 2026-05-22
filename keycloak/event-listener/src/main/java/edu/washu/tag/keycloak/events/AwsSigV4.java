package edu.washu.tag.keycloak.events;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Instant;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.util.Locale;
import java.util.Map;
import java.util.TreeMap;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;

/**
 * AWS Signature V4 implementation for S3 PUT requests against MinIO.
 *
 * <p>Hand-rolled rather than pulling in an S3 SDK because the only operation
 * the OPA bundle publisher makes against MinIO is PUT object — a few hundred
 * bytes of signing code beats a multi-megabyte SDK dependency that would
 * also pull in transitive HTTP / crypto libraries with their own CVE stream.
 * The Sig V4 algorithm itself is well-specified and stable; this code
 * mirrors the steps in <a
 * href="https://docs.aws.amazon.com/general/latest/gr/sigv4_signing.html">AWS's
 * reference</a>. Pure JDK — no third-party crypto, no Bouncy Castle.
 */
final class AwsSigV4 {

    private static final String ALGORITHM = "AWS4-HMAC-SHA256";
    private static final String SERVICE = "s3";
    private static final String TERMINATOR = "aws4_request";
    private static final DateTimeFormatter AMZ_DATE_FORMAT =
            DateTimeFormatter.ofPattern("yyyyMMdd'T'HHmmss'Z'").withZone(ZoneOffset.UTC);
    private static final DateTimeFormatter DATESTAMP_FORMAT =
            DateTimeFormatter.ofPattern("yyyyMMdd").withZone(ZoneOffset.UTC);

    private AwsSigV4() {
    }

    /**
     * Computed Sig V4 headers ready to be attached to an outbound HTTP request.
     * The Host header is computed externally (URI-derived) and not included here.
     */
    record Headers(String amzDate, String contentSha256, String authorization) {
    }

    /**
     * Sign an S3 PUT request. The body must already be in its final
     * encoded form; we sha256 it for the content-hash header.
     *
     * @param accessKey  the bucket-write service account access key
     * @param secretKey  the bucket-write service account secret key
     * @param region     the S3 region (any valid string for MinIO — we use us-east-1)
     * @param host       Host header value, e.g. {@code minio.scout-storage.svc:9000}
     * @param canonicalPath URL-encoded path including a leading slash (e.g. {@code /opa-bundles/scout/bundle.tar.gz})
     * @param body       the request body bytes (the bundle tar.gz)
     * @param now        the current time (parameterized for testability)
     * @return signed headers including authorization, x-amz-date, x-amz-content-sha256
     */
    static Headers signPut(String accessKey, String secretKey, String region,
                           String host, String canonicalPath, byte[] body, Instant now) {
        String amzDate = AMZ_DATE_FORMAT.format(now);
        String dateStamp = DATESTAMP_FORMAT.format(now);
        String contentSha256 = hex(sha256(body));

        Map<String, String> headers = new TreeMap<>();
        headers.put("host", host);
        headers.put("x-amz-content-sha256", contentSha256);
        headers.put("x-amz-date", amzDate);

        String signedHeaders = String.join(";", headers.keySet());
        String canonicalHeaders = canonicalHeaders(headers);
        String canonicalRequest = String.join("\n",
                "PUT",
                canonicalPath,
                "",
                canonicalHeaders,
                signedHeaders,
                contentSha256);

        String credentialScope = String.join("/", dateStamp, region, SERVICE, TERMINATOR);
        String stringToSign = String.join("\n",
                ALGORITHM,
                amzDate,
                credentialScope,
                hex(sha256(canonicalRequest.getBytes(StandardCharsets.UTF_8))));

        byte[] signingKey = deriveSigningKey(secretKey, dateStamp, region);
        String signature = hex(hmacSha256(signingKey, stringToSign.getBytes(StandardCharsets.UTF_8)));

        String authorization = String.format(Locale.ROOT,
                "%s Credential=%s/%s, SignedHeaders=%s, Signature=%s",
                ALGORITHM,
                accessKey,
                credentialScope,
                signedHeaders,
                signature);

        return new Headers(amzDate, contentSha256, authorization);
    }

    private static String canonicalHeaders(Map<String, String> sortedHeaders) {
        StringBuilder sb = new StringBuilder();
        for (Map.Entry<String, String> e : sortedHeaders.entrySet()) {
            sb.append(e.getKey()).append(':').append(e.getValue().trim()).append('\n');
        }
        return sb.toString();
    }

    private static byte[] deriveSigningKey(String secretKey, String dateStamp, String region) {
        // Names follow AWS Sig V4 reference (kSecret/kDate/kRegion/kService/
        // kSigning) but lowercased to match the project's variable-name
        // checkstyle rule. The derivation chain is unchanged.
        byte[] secretBytes = ("AWS4" + secretKey).getBytes(StandardCharsets.UTF_8);
        byte[] dateBytes = hmacSha256(secretBytes, dateStamp.getBytes(StandardCharsets.UTF_8));
        byte[] regionBytes = hmacSha256(dateBytes, region.getBytes(StandardCharsets.UTF_8));
        byte[] serviceBytes = hmacSha256(regionBytes, SERVICE.getBytes(StandardCharsets.UTF_8));
        return hmacSha256(serviceBytes, TERMINATOR.getBytes(StandardCharsets.UTF_8));
    }

    private static byte[] hmacSha256(byte[] key, byte[] data) {
        try {
            Mac mac = Mac.getInstance("HmacSHA256");
            mac.init(new SecretKeySpec(key, "HmacSHA256"));
            return mac.doFinal(data);
        } catch (Exception e) {
            // HmacSHA256 is mandatory in every Java implementation; this
            // cannot fail in practice. Wrap as runtime so callers don't
            // need a checked-exception path for an impossible case.
            throw new IllegalStateException("HmacSHA256 unavailable", e);
        }
    }

    private static byte[] sha256(byte[] data) {
        try {
            MessageDigest md = MessageDigest.getInstance("SHA-256");
            return md.digest(data);
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException("SHA-256 unavailable", e);
        }
    }

    private static String hex(byte[] bytes) {
        StringBuilder sb = new StringBuilder(bytes.length * 2);
        for (byte b : bytes) {
            sb.append(String.format(Locale.ROOT, "%02x", b));
        }
        return sb.toString();
    }
}
