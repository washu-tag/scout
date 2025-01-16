package edu.washu.tag.temporal.activity;

import io.temporal.testing.TestActivityExtension;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.RegisterExtension;
import org.junit.jupiter.api.io.TempDir;

import java.nio.file.Files;
import java.nio.file.Path;

import static org.junit.jupiter.api.Assertions.assertEquals;

public class TestSplitHl7LogActivity {
    // TODO I need to create a mock file handler I think?
//    @RegisterExtension
//    public static final TestActivityExtension activityExtension = TestActivityExtension.newBuilder()
//            .setActivityImplementations(new SplitHl7LogActivityImpl()).build();
//
//    @Test
//    public void testFindHl7LogFileActivity(SplitHl7LogActivity activity, @TempDir Path tempDir) throws Exception{
//        String date = "2021-08-01";
//        String otherDate = "2021-08-02";
//
//        Path logPath = tempDir.resolve("hl7_" + date + ".log");
//        Files.createFile(logPath);
//        Path otherLogPath = tempDir.resolve("hl7_" + otherDate + ".log");
//        Files.createFile(otherLogPath);
//
//        FindHl7LogFileInput input = new FindHl7LogFileInput(date, tempDir.toString());
//        FindHl7LogFileOutput output = activity.findHl7LogFile(input);
//        assertEquals(logPath.toString(), output.logFileAbsPath());
//    }

    // TODO this is not testable right now because we are hard-coding a path to a script that the test code won't be able to find
//    @Test
//    public void testSplitHl7Log(SplitHl7LogActivity activity, @TempDir Path tempDir) throws Exception{
//        List<String> lines = List.of("line1", "line2");
//        String contents = String.join("\r", lines);
//
//        Path logPath = tempDir.resolve("hl7.log");
//        Files.writeString(logPath, contents);
//
//        Path outputDir = tempDir.resolve("output");
//        SplitHl7LogActivityInput input = new SplitHl7LogActivityInput(logPath.toString(), outputDir.toString());
//        SplitHl7LogActivityOutput output = activity.splitHl7Log(input);
//
//    }
}
