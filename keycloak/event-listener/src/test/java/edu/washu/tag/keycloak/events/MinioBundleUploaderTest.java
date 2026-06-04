package edu.washu.tag.keycloak.events;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import software.amazon.awssdk.core.sync.RequestBody;
import software.amazon.awssdk.services.s3.S3Client;
import software.amazon.awssdk.services.s3.model.PutObjectRequest;
import software.amazon.awssdk.services.s3.model.S3Exception;

/**
 * Tests upload()'s success/failure -> boolean contract — the value the
 * factory's retry policy hinges on (a false return triggers a reschedule). The
 * S3 client is injected via the package-private test constructor, so no live
 * S3 endpoint is needed.
 */
class MinioBundleUploaderTest {

    private S3Client s3;
    private MinioBundleUploader uploader;

    @BeforeEach
    void setUp() {
        s3 = mock(S3Client.class);
        uploader = new MinioBundleUploader(s3, "bucket", "scout/bundle.tar.gz");
    }

    @Test
    void upload_returns_true_on_success() {
        // Default mock: putObject returns without throwing -> success.
        assertTrue(uploader.upload(new byte[] {1, 2, 3}));
    }

    @Test
    void upload_returns_false_on_s3_exception() {
        when(s3.putObject(any(PutObjectRequest.class), any(RequestBody.class)))
                .thenThrow(S3Exception.builder().statusCode(500).message("boom").build());
        assertFalse(uploader.upload(new byte[] {1, 2, 3}));
    }

    @Test
    void upload_returns_false_on_unexpected_exception() {
        when(s3.putObject(any(PutObjectRequest.class), any(RequestBody.class)))
                .thenThrow(new RuntimeException("connection reset"));
        assertFalse(uploader.upload(new byte[] {1, 2, 3}));
    }
}
