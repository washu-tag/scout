package edu.washu.tag.extractor.hl7log;


import static org.hamcrest.MatcherAssert.assertThat;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;

import edu.washu.tag.extractor.hl7log.activity.FindHl7LogsActivity;
import edu.washu.tag.extractor.hl7log.activity.FindHl7LogsActivityImpl;
import edu.washu.tag.extractor.hl7log.model.FindHl7LogFileInput;
import edu.washu.tag.extractor.hl7log.model.FindHl7LogFileOutput;
import edu.washu.tag.extractor.hl7log.util.FileHandler;
import io.temporal.testing.TestActivityExtension;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Set;
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

    private Path match;
    private Path mismatch;
    private Path hidden;
    private Path notLog;

    @BeforeEach
    void setUp() throws IOException {
        match = Files.createFile(tempDir.resolve("20250105.log"));
        mismatch = Files.createFile(tempDir.resolve("20250106.log"));
        hidden = Files.createFile(tempDir.resolve(".20250105.log.asef"));
        notLog = Files.createFile(tempDir.resolve("20250105.txt"));
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

        assertThat(out.logFiles(), Matchers.containsInAnyOrder(List.of(match.toString(), mismatch.toString()),
            "Only log files should be returned"));
        assertNull(out.continued(), "No Continue-As-New because file count < concurrency");
    }

    @Test
    void testFindLogFiles_excludesFilesCorrectlyWithDate(FindHl7LogsActivity activityStub) {
        FindHl7LogFileInput input = new FindHl7LogFileInput(
            List.of(tempDir.toString()),
            "20250105",
            null,
            null,
            Integer.MAX_VALUE
        );

        FindHl7LogFileOutput out = activityStub.findHl7LogFiles(input);

        assertEquals(List.of(match.toString()), out.logFiles(), "Only the matching date file should be returned");
        assertNull(out.continued(), "No Continue-As-New because file count < concurrency");
    }

    @Test
    void testFindLogFiles_visitFileFailed(FindHl7LogsActivity activityStub) throws Exception {
        // File that will raise AccessDeniedException
        Path noPerms = Files.createFile(tempDir.resolve("privateFile"));
        Files.setPosixFilePermissions(noPerms, Set.of());   // chmod 000

        // Dir that will raise AccessDeniedException
        Path noPermsDir = Files.createFile(tempDir.resolve("privateDir"));
        Files.setPosixFilePermissions(noPermsDir, Set.of());   // chmod 000

        FindHl7LogFileInput input = new FindHl7LogFileInput(
            List.of(tempDir.toString()),
            "20250105",
            null,
            null,
            Integer.MAX_VALUE
        );

        FindHl7LogFileOutput out = activityStub.findHl7LogFiles(input);

        assertEquals(List.of(match.toString()), out.logFiles(), "Only the matching date file should be returned");
        assertNull(out.continued(), "No Continue-As-New because file count < concurrency");
    }
}
