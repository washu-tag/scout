package edu.washu.tag.temporal.activity;

import edu.washu.tag.temporal.model.SplitHl7LogActivityInput;
import edu.washu.tag.temporal.model.SplitHl7LogActivityOutput;
import edu.washu.tag.temporal.model.TransformSplitHl7LogInput;
import edu.washu.tag.temporal.model.TransformSplitHl7LogOutput;
import edu.washu.tag.temporal.model.FindHl7LogFileInput;
import edu.washu.tag.temporal.model.FindHl7LogFileOutput;
import io.temporal.activity.Activity;
import io.temporal.spring.boot.ActivityImpl;

import java.io.File;
import java.io.IOException;
import java.nio.file.Path;
import java.util.Arrays;

@ActivityImpl(workers = "ingest-hl7-log")
public class SplitHl7LogActivityImpl implements SplitHl7LogActivity {
    private String runScript(File cwd, String... command) {
        try {
            Process p = new ProcessBuilder()
                    .directory(cwd)
                    .command(command)
                    .start();
            int exitCode = p.waitFor();
            if (exitCode != 0) {
                String stderr = new String(p.getErrorStream().readAllBytes());
                throw Activity.wrap(new RuntimeException(command[0] + " failed with exit code " + exitCode + ". stderr: " + stderr));
            }
            return new String(p.getInputStream().readAllBytes());
        } catch (IOException | InterruptedException e) {
            throw Activity.wrap(e);
        }
    }

    @Override
    public FindHl7LogFileOutput findHl7LogFile(FindHl7LogFileInput input) {
        File logsDir = Path.of(input.logsDir()).toFile();
        File[] logFiles = logsDir.listFiles((dir, name) -> name.contains(input.date()));
        if (logFiles == null || logFiles.length != 1) {
            throw Activity.wrap(new RuntimeException("Expected exactly one file with date " + input.date() + " in " + input.logsDir() + ". Found " + (logFiles == null ? 0 : logFiles.length)));
        }

        return new FindHl7LogFileOutput(logFiles[0].getAbsolutePath());
    }

    @Override
    public SplitHl7LogActivityOutput splitHl7Log(SplitHl7LogActivityInput input) {
        String stdout = runScript(Path.of(input.outputPath()).toFile(), "/usr/local/bin/split-hl7-log.sh", input.logFilePath());
        return new SplitHl7LogActivityOutput(input.outputPath(), Arrays.stream(stdout.split("\n")).toList());
    }

    @Override
    public TransformSplitHl7LogOutput transformSplitHl7Log(TransformSplitHl7LogInput input) {
        String stdout = runScript(Path.of(input.rootOutputPath()).toFile(), "/usr/local/bin/transform-split-hl7-log.sh", input.splitLogFile());
        return new TransformSplitHl7LogOutput(input.rootOutputPath(), stdout);
    }
}
