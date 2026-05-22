package edu.washu.tag.keycloak.events;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.nio.charset.StandardCharsets;
import java.time.Instant;
import org.junit.jupiter.api.Test;

/**
 * Unit tests for the hand-rolled AWS Sig V4 signer. The signer is small
 * enough to be obviously-correct on inspection, but the consequences of
 * a quiet bug (silent 403 from MinIO) are bad enough that explicit
 * regression coverage of the canonical-request structure earns its keep.
 */
class AwsSigV4Test {

    @Test
    void signature_is_deterministic_for_fixed_inputs() {
        Instant now = Instant.parse("2026-05-22T12:00:00Z");
        byte[] body = "hello".getBytes(StandardCharsets.UTF_8);

        AwsSigV4.Headers first = AwsSigV4.signPut(
                "AKIA_TEST", "SECRET_TEST", "us-east-1",
                "minio.scout-storage.svc:9000",
                "/opa-bundles/scout/bundle.tar.gz",
                body, now);
        AwsSigV4.Headers second = AwsSigV4.signPut(
                "AKIA_TEST", "SECRET_TEST", "us-east-1",
                "minio.scout-storage.svc:9000",
                "/opa-bundles/scout/bundle.tar.gz",
                body, now);

        assertEquals(first.amzDate(), second.amzDate());
        assertEquals(first.contentSha256(), second.contentSha256());
        assertEquals(first.authorization(), second.authorization());
    }

    @Test
    void different_bodies_yield_different_signatures() {
        Instant now = Instant.parse("2026-05-22T12:00:00Z");
        AwsSigV4.Headers a = AwsSigV4.signPut(
                "AKIA_TEST", "SECRET_TEST", "us-east-1",
                "minio:9000", "/bucket/key",
                "payload-A".getBytes(StandardCharsets.UTF_8), now);
        AwsSigV4.Headers b = AwsSigV4.signPut(
                "AKIA_TEST", "SECRET_TEST", "us-east-1",
                "minio:9000", "/bucket/key",
                "payload-B".getBytes(StandardCharsets.UTF_8), now);

        // Different bodies → different content-sha256 → different
        // string-to-sign → different signature. Cheap end-to-end check
        // that the body hash actually feeds the signature.
        assertTrue(!a.contentSha256().equals(b.contentSha256()));
        assertTrue(!a.authorization().equals(b.authorization()));
    }

    @Test
    void authorization_header_format_matches_aws_spec() {
        Instant now = Instant.parse("2026-05-22T12:00:00Z");
        AwsSigV4.Headers h = AwsSigV4.signPut(
                "AKIATESTACCESS", "secretvalue", "us-west-2",
                "host:9000", "/b/k",
                new byte[0], now);

        // Expected shape:
        //   AWS4-HMAC-SHA256 Credential=<AK>/<YYYYMMDD>/<region>/s3/aws4_request, SignedHeaders=host;x-amz-content-sha256;x-amz-date, Signature=<64 hex>
        assertTrue(h.authorization().startsWith("AWS4-HMAC-SHA256 Credential=AKIATESTACCESS/20260522/us-west-2/s3/aws4_request, "),
                "Auth header credential scope wrong: " + h.authorization());
        assertTrue(h.authorization().contains("SignedHeaders=host;x-amz-content-sha256;x-amz-date"),
                "Auth header signed-headers wrong: " + h.authorization());
        assertTrue(h.authorization().matches(".*Signature=[0-9a-f]{64}$"),
                "Auth header signature shape wrong: " + h.authorization());
    }

    @Test
    void empty_body_uses_sha256_of_empty_string() {
        AwsSigV4.Headers h = AwsSigV4.signPut(
                "AK", "SK", "us-east-1",
                "host", "/p",
                new byte[0], Instant.parse("2026-05-22T00:00:00Z"));

        // sha256("") = e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
        assertEquals("e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
                h.contentSha256());
    }

    @Test
    void amz_date_format_is_iso_basic_utc() {
        AwsSigV4.Headers h = AwsSigV4.signPut(
                "AK", "SK", "us-east-1",
                "host", "/p",
                new byte[0], Instant.parse("2026-05-22T14:30:15Z"));
        assertEquals("20260522T143015Z", h.amzDate());
    }
}
