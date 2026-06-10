package edu.washu.tag.extractor.hl7log;


import static org.hamcrest.MatcherAssert.assertThat;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import edu.washu.tag.extractor.hl7log.activity.FindHl7LogsActivity;
import edu.washu.tag.extractor.hl7log.activity.FindHl7LogsActivityImpl;
import edu.washu.tag.extractor.hl7log.model.FindHl7LogFileInput;
import edu.washu.tag.extractor.hl7log.model.FindHl7LogFileOutput;
import edu.washu.tag.extractor.hl7log.util.FileHandler;
import io.temporal.testing.TestActivityExtension;
import java.io.IOException;
import java.net.URI;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import org.hamcrest.Matchers;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.RegisterExtension;
import org.junit.jupiter.api.io.TempDir;
import org.mockito.Mockito;
import org.springframework.boot.test.context.SpringBootTest;

@SpringBootTest
class FindLogFileTest {

    @TempDir Path tempDir;

    // FileHandler is required by the activity but not used in this scenario
    private static final FileHandler fileHandler = Mockito.mock(FileHandler.class);

    @RegisterExtension
    static final TestActivityExtension activityExt =
        TestActivityExtension.newBuilder()
            .setActivityImplementations(new FindHl7LogsActivityImpl(fileHandler))
            .build();

    private final String date = "20250105";
    private Path matchingDateLog;
    private Path alernativeDateLog;

    @BeforeEach
    void setUp() throws IOException {
        Mockito.reset(fileHandler);
        matchingDateLog = Files.createFile(tempDir.resolve(date + ".log"));
        alernativeDateLog = Files.createFile(tempDir.resolve("20250106.log"));
        Files.createFile(tempDir.resolve("." + date + ".log.asef"));
        Files.createFile(tempDir.resolve("20250105.txt"));
    }

    @Test
    void testFindLogFiles_excludesFilesCorrectly(FindHl7LogsActivity activityStub) {
        FindHl7LogFileInput input = new FindHl7LogFileInput(
            List.of(tempDir.toString()),
            null,
            null,
            null,
            Integer.MAX_VALUE
        );

        FindHl7LogFileOutput out = activityStub.findHl7LogFiles(input);

        assertThat(out.logFiles(), Matchers.containsInAnyOrder(matchingDateLog.toString(), alernativeDateLog.toString()));
        assertNull(out.continued(), "No Continue-As-New because file count < concurrency");
    }

    @Test
    void testFindLogFiles_excludesFilesCorrectlyWithDate(FindHl7LogsActivity activityStub) {
        FindHl7LogFileInput input = new FindHl7LogFileInput(
            List.of(tempDir.toString()),
            date,
            null,
            null,
            Integer.MAX_VALUE
        );

        FindHl7LogFileOutput out = activityStub.findHl7LogFiles(input);

        assertEquals(List.of(matchingDateLog.toString()), out.logFiles(), "Only the matching date file should be returned");
        assertNull(out.continued(), "No Continue-As-New because file count < concurrency");
    }

    @Test
    void testFindLogFiles_s3Prefix_listsAndFiltersFileNames(FindHl7LogsActivity activityStub) {
        String prefix = "s3://logs-bucket/hl7/";
        when(fileHandler.isFile(URI.create(prefix))).thenReturn(false);
        when(fileHandler.ls(URI.create(prefix))).thenReturn(List.of(
            "s3://logs-bucket/hl7/20250105.log",
            "s3://logs-bucket/hl7/20250106.log",
            "s3://logs-bucket/hl7/.20250105.log",       // hidden, excluded
            "s3://logs-bucket/hl7/20250105.txt"          // wrong extension, excluded
        ));

        FindHl7LogFileInput input = new FindHl7LogFileInput(
            List.of(prefix),
            null,
            null,
            null,
            Integer.MAX_VALUE
        );

        FindHl7LogFileOutput out = activityStub.findHl7LogFiles(input);

        assertThat(out.logFiles(), Matchers.containsInAnyOrder(
            "s3://logs-bucket/hl7/20250105.log",
            "s3://logs-bucket/hl7/20250106.log"
        ));
        assertNull(out.continued());
    }

    @Test
    void testFindLogFiles_s3Prefix_appliesDateFilter(FindHl7LogsActivity activityStub) {
        String prefix = "s3://logs-bucket/hl7/";
        when(fileHandler.isFile(URI.create(prefix))).thenReturn(false);
        when(fileHandler.ls(URI.create(prefix))).thenReturn(List.of(
            "s3://logs-bucket/hl7/20250105.log",
            "s3://logs-bucket/hl7/20250106.log"
        ));

        FindHl7LogFileInput input = new FindHl7LogFileInput(
            List.of(prefix),
            date,
            null,
            null,
            Integer.MAX_VALUE
        );

        FindHl7LogFileOutput out = activityStub.findHl7LogFiles(input);

        assertEquals(List.of("s3://logs-bucket/hl7/20250105.log"), out.logFiles());
    }

    @Test
    void testFindLogFiles_s3SingleFile_skipsListing(FindHl7LogsActivity activityStub) {
        String fileUri = "s3://logs-bucket/hl7/20250105.log";
        when(fileHandler.isFile(URI.create(fileUri))).thenReturn(true);

        FindHl7LogFileInput input = new FindHl7LogFileInput(
            List.of(fileUri),
            null,
            null,
            null,
            Integer.MAX_VALUE
        );

        FindHl7LogFileOutput out = activityStub.findHl7LogFiles(input);

        assertEquals(List.of(fileUri), out.logFiles());
        verify(fileHandler, never()).ls(any());
    }

    @Test
    void testFindLogFiles_s3PrefixWithoutTrailingSlash_listsViaHeadObject(FindHl7LogsActivity activityStub) {
        // User forgets the trailing slash — HeadObject reports "no such key" so we list the prefix.
        String prefix = "s3://logs-bucket/hl7";
        when(fileHandler.isFile(URI.create(prefix))).thenReturn(false);
        when(fileHandler.ls(URI.create(prefix))).thenReturn(List.of(
            "s3://logs-bucket/hl7/20250105.log",
            "s3://logs-bucket/hl7/20250106.log"
        ));

        FindHl7LogFileInput input = new FindHl7LogFileInput(
            List.of(prefix),
            null,
            null,
            null,
            Integer.MAX_VALUE
        );

        FindHl7LogFileOutput out = activityStub.findHl7LogFiles(input);

        assertThat(out.logFiles(), Matchers.containsInAnyOrder(
            "s3://logs-bucket/hl7/20250105.log",
            "s3://logs-bucket/hl7/20250106.log"
        ));
    }
}
