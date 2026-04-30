package edu.washu.tag.extractor.hl7log.util;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.when;

import java.io.IOException;
import java.net.URI;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.function.Consumer;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.mockito.Mockito;
import software.amazon.awssdk.awscore.exception.AwsErrorDetails;
import software.amazon.awssdk.services.s3.S3Client;
import software.amazon.awssdk.services.s3.model.HeadObjectResponse;
import software.amazon.awssdk.services.s3.model.NoSuchKeyException;
import software.amazon.awssdk.services.s3.model.S3Exception;

class FileHandlerImplTest {

    private S3Client s3Client;
    private FileHandlerImpl fileHandler;

    @BeforeEach
    void setUp() {
        s3Client = Mockito.mock(S3Client.class);
        fileHandler = new FileHandlerImpl(s3Client);
    }

    @Test
    @SuppressWarnings("unchecked")
    void isFile_s3ObjectExists_returnsTrue() {
        when(s3Client.headObject(any(Consumer.class))).thenReturn(HeadObjectResponse.builder().build());

        assertTrue(fileHandler.isFile(URI.create("s3://bucket/key.log")));
    }

    @Test
    @SuppressWarnings("unchecked")
    void isFile_noSuchKeyException_returnsFalse() {
        when(s3Client.headObject(any(Consumer.class))).thenThrow(NoSuchKeyException.builder().message("404").build());

        assertFalse(fileHandler.isFile(URI.create("s3://bucket/missing.log")));
    }

    @Test
    @SuppressWarnings("unchecked")
    void isFile_s3ExceptionWith404_returnsFalse() {
        // Some S3-compatible services return a generic S3Exception with status 404 instead of
        // mapping to the typed NoSuchKeyException — handle both.
        S3Exception generic404 = (S3Exception) S3Exception.builder()
            .statusCode(404)
            .awsErrorDetails(AwsErrorDetails.builder().errorCode("NotFound").build())
            .message("not found")
            .build();
        when(s3Client.headObject(any(Consumer.class))).thenThrow(generic404);

        assertFalse(fileHandler.isFile(URI.create("s3://bucket/missing.log")));
    }

    @Test
    @SuppressWarnings("unchecked")
    void isFile_s3ExceptionNon404_propagates() {
        S3Exception forbidden = (S3Exception) S3Exception.builder()
            .statusCode(403)
            .awsErrorDetails(AwsErrorDetails.builder().errorCode("AccessDenied").build())
            .message("forbidden")
            .build();
        when(s3Client.headObject(any(Consumer.class))).thenThrow(forbidden);

        S3Exception thrown = assertThrows(S3Exception.class,
            () -> fileHandler.isFile(URI.create("s3://bucket/restricted.log")));
        assertEquals(403, thrown.statusCode());
    }

    @Test
    void isFile_filesystemRegularFile_returnsTrue(@TempDir Path tempDir) throws IOException {
        Path file = Files.createFile(tempDir.resolve("real.log"));
        assertTrue(fileHandler.isFile(file.toUri()));
    }

    @Test
    void isFile_filesystemDirectory_returnsFalse(@TempDir Path tempDir) {
        assertFalse(fileHandler.isFile(tempDir.toUri()));
    }

    @Test
    void isFile_filesystemMissing_returnsFalse(@TempDir Path tempDir) {
        assertFalse(fileHandler.isFile(tempDir.resolve("nope.log").toUri()));
    }
}
