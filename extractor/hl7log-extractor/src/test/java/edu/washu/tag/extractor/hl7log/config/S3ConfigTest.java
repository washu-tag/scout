package edu.washu.tag.extractor.hl7log.config;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertSame;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import org.junit.jupiter.api.Test;
import software.amazon.awssdk.core.SdkRequest;
import software.amazon.awssdk.core.interceptor.ExecutionAttributes;
import software.amazon.awssdk.services.s3.model.GetObjectRequest;
import software.amazon.awssdk.services.s3.model.PutObjectRequest;
import software.amazon.awssdk.services.s3.model.ServerSideEncryption;

class S3ConfigTest {

    private static SdkRequest intercept(SdkRequest request) {
        return new S3Config.SseS3Interceptor()
                .modifyRequest(() -> request, new ExecutionAttributes());
    }

    @Test
    void isAwsS3_noEndpointOverrideOrAmazonawsHost_returnsTrue() {
        assertTrue(S3Config.isAwsS3(null));
        assertTrue(S3Config.isAwsS3(""));
        assertTrue(S3Config.isAwsS3("   "));
        assertTrue(S3Config.isAwsS3("https://s3.us-east-1.amazonaws.com"));
    }

    @Test
    void isAwsS3_s3CompatibleEndpoint_returnsFalse() {
        assertFalse(S3Config.isAwsS3("http://minio.storage.svc:9000"));
        assertFalse(S3Config.isAwsS3("http://localhost:9000"));
    }

    @Test
    void requestsSse_s3_returnsTrue() {
        assertTrue(S3Config.requestsSse("S3"));
        assertTrue(S3Config.requestsSse("s3"));
    }

    @Test
    void requestsSse_noneOrUnset_returnsFalse() {
        assertFalse(S3Config.requestsSse("NONE"));
        assertFalse(S3Config.requestsSse("none"));
        assertFalse(S3Config.requestsSse(""));
        assertFalse(S3Config.requestsSse(null));
    }

    @Test
    void requestsSse_unknownValue_throws() {
        assertThrows(IllegalArgumentException.class, () -> S3Config.requestsSse("KMS"));
    }

    @Test
    void modifyRequest_putObjectWithoutEncryption_addsAes256() {
        PutObjectRequest request = PutObjectRequest.builder().bucket("b").key("k").build();

        SdkRequest modified = intercept(request);

        assertEquals(ServerSideEncryption.AES256,
                ((PutObjectRequest) modified).serverSideEncryption());
    }

    @Test
    void modifyRequest_putObjectWithEncryption_isLeftAlone() {
        PutObjectRequest request = PutObjectRequest.builder()
                .bucket("b")
                .key("k")
                .serverSideEncryption(ServerSideEncryption.AWS_KMS)
                .build();

        assertSame(request, intercept(request));
    }

    @Test
    void modifyRequest_nonUploadRequest_passesThrough() {
        GetObjectRequest request = GetObjectRequest.builder().bucket("b").key("k").build();

        assertSame(request, intercept(request));
    }
}
