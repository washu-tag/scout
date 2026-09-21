package edu.washu.tag.extractor.hl7log.config;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertSame;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.Mockito.when;

import org.junit.jupiter.api.Test;
import org.mockito.Mockito;
import software.amazon.awssdk.core.SdkRequest;
import software.amazon.awssdk.core.interceptor.Context;
import software.amazon.awssdk.core.interceptor.ExecutionAttributes;
import software.amazon.awssdk.services.s3.model.CreateMultipartUploadRequest;
import software.amazon.awssdk.services.s3.model.GetObjectRequest;
import software.amazon.awssdk.services.s3.model.PutObjectRequest;
import software.amazon.awssdk.services.s3.model.ServerSideEncryption;

class S3ConfigTest {

    private static SdkRequest intercept(SdkRequest request) {
        Context.ModifyRequest context = Mockito.mock(Context.ModifyRequest.class);
        when(context.request()).thenReturn(request);
        return new S3Config.SseS3Interceptor()
                .modifyRequest(context, new ExecutionAttributes());
    }

    @Test
    void no_endpoint_is_aws() {
        assertTrue(S3Config.isAwsS3(null));
        assertTrue(S3Config.isAwsS3(""));
        assertTrue(S3Config.isAwsS3("   "));
    }

    @Test
    void amazonaws_endpoint_is_aws() {
        assertTrue(S3Config.isAwsS3("https://s3.us-east-1.amazonaws.com"));
    }

    @Test
    void minio_endpoint_is_not_aws() {
        assertFalse(S3Config.isAwsS3("http://minio.storage.svc:9000"));
        assertFalse(S3Config.isAwsS3("http://localhost:9000"));
    }

    @Test
    void put_object_gets_sse_s3() {
        PutObjectRequest request = PutObjectRequest.builder().bucket("b").key("k").build();
        assertNull(request.serverSideEncryption());

        SdkRequest modified = intercept(request);

        assertEquals(ServerSideEncryption.AES256,
                ((PutObjectRequest) modified).serverSideEncryption());
    }

    @Test
    void multipart_upload_gets_sse_s3() {
        CreateMultipartUploadRequest request =
                CreateMultipartUploadRequest.builder().bucket("b").key("k").build();

        SdkRequest modified = intercept(request);

        assertEquals(ServerSideEncryption.AES256,
                ((CreateMultipartUploadRequest) modified).serverSideEncryption());
    }

    @Test
    void existing_encryption_is_left_alone() {
        PutObjectRequest request = PutObjectRequest.builder()
                .bucket("b")
                .key("k")
                .serverSideEncryption(ServerSideEncryption.AWS_KMS)
                .build();

        SdkRequest modified = intercept(request);

        assertSame(request, modified);
        assertEquals(ServerSideEncryption.AWS_KMS,
                ((PutObjectRequest) modified).serverSideEncryption());
    }

    @Test
    void non_upload_requests_pass_through() {
        GetObjectRequest request = GetObjectRequest.builder().bucket("b").key("k").build();

        assertSame(request, intercept(request));
    }
}
