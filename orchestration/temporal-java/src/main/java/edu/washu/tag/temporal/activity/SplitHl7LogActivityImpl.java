package edu.washu.tag.temporal.activity;

import edu.washu.tag.temporal.model.SplitHl7LogActivityInput;
import edu.washu.tag.temporal.model.SplitHl7LogActivityOutput;
import edu.washu.tag.temporal.model.TransformSplitHl7LogInput;
import edu.washu.tag.temporal.model.TransformSplitHl7LogOutput;
import io.temporal.activity.Activity;
import io.temporal.spring.boot.ActivityImpl;

import java.io.File;
import java.io.IOException;
import java.nio.file.Path;
import java.util.Arrays;

@ActivityImpl(taskQueues = "ingest-hl7-log")
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
                String commandName = command.length > 0 ? command[0] : "<unknown>";
                throw Activity.wrap(new RuntimeException(commandName + " failed with exit code " + exitCode + ". stderr: " + stderr));
            }
            return new String(p.getInputStream().readAllBytes());
        } catch (IOException | InterruptedException e) {
            throw Activity.wrap(e);
        }
    }

    @Override
    public SplitHl7LogActivityOutput splitHl7Log(SplitHl7LogActivityInput input) {
        // TODO configure the path to the script
        String stdout = runScript(Path.of(input.outputPath()).toFile(), "/app/scripts/split-hl7-log.sh", input.logFilePath());
        return new SplitHl7LogActivityOutput(input.outputPath(), Arrays.stream(stdout.split("\n")).toList());
    }

    @Override
    public TransformSplitHl7LogOutput transformSplitHl7Log(TransformSplitHl7LogInput input) {
        // TODO configure the path to the script
        String stdout = runScript(Path.of(input.rootOutputPath()).toFile(), "/app/scripts/transform-split-hl7-log.sh", input.splitLogFile());
        return new TransformSplitHl7LogOutput(input.rootOutputPath(), stdout);
    }
}
