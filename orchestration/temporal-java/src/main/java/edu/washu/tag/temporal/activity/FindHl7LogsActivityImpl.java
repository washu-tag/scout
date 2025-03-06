package edu.washu.tag.temporal.activity;

import edu.washu.tag.temporal.model.FindHl7LogFileInput;
import edu.washu.tag.temporal.model.FindHl7LogFileOutput;
import edu.washu.tag.temporal.workflow.Hl7FromHl7LogWorkflow;
import io.temporal.activity.Activity;
import io.temporal.activity.ActivityInfo;
import io.temporal.failure.ApplicationFailure;
import io.temporal.spring.boot.ActivityImpl;
import io.temporal.workflow.ChildWorkflowOptions;
import io.temporal.workflow.Workflow;
import org.slf4j.Logger;
import org.springframework.stereotype.Component;

import java.io.File;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Collections;
import java.util.List;

@Component
@ActivityImpl(taskQueues = "ingest-hl7-log")
public class FindHl7LogsActivityImpl implements FindHl7LogsActivity {
    private static final Logger logger = Workflow.getLogger(FindHl7LogsActivityImpl.class);

    @Override
    public FindHl7LogFileOutput findHl7LogFiles(FindHl7LogFileInput input) {
        ActivityInfo activityInfo = Activity.getExecutionContext().getInfo();
        logger.info("WorkflowId {} ActivityId {} - Finding HL7 log files for input {}", activityInfo.getWorkflowId(), activityInfo.getActivityId(), input);
        List<String> logFiles;
        if (input.logPaths() != null && !input.logPaths().isEmpty()) {
            // Case 1: We were given explicit log paths
            logFiles = findLogFilesForPaths(input.logPaths());
        } else if (input.logsRootPath() != null && !input.logsRootPath().isEmpty() && input.date() != null && !input.date().isEmpty()) {
            // Case 2: We were given a root path and a date
            String logFile = findLogFileForDate(input.logsRootPath(), input.date());
            logFiles = List.of(logFile);
        } else {
            // Case 3: We have nothing
            logFiles = Collections.emptyList();
        }

        logger.info("WorkflowId {} ActivityId {} - Found {} log files", activityInfo.getWorkflowId(), activityInfo.getActivityId(), logFiles.size());

        if (logFiles.isEmpty()) {
            throw ApplicationFailure.newFailure("No log files found for input " + input, "type");
        }

        return new FindHl7LogFileOutput(logFiles);
    }

    /**
     * Find log files for the given paths
     * If the path is a directory, find all files in the directory recursively
     * @param logPaths list of paths to search for log files
     * @return list of log files
     */
    private List<String> findLogFilesForPaths(List<String> logPaths) {
        ActivityInfo activityInfo = Activity.getExecutionContext().getInfo();
        return logPaths.stream()
                .map(logPath -> {
                    logger.info("WorkflowId {} ActivityId {} - Finding HL7 log files for path {}", activityInfo.getWorkflowId(), activityInfo.getActivityId(), logPath);
                    File logFile = new File(logPath);
                    if (logFile.isDirectory()) {
                        try {
                            return Files.walk(logFile.toPath())
                                    .filter(Files::isRegularFile)
                                    .map(Path::toString)
                                    .filter(s -> s.endsWith(".log"))
                                    .toList();
                        } catch (IOException e) {
                            logger.warn("WorkflowId {} ActivityId {} - Error finding log files in directory {}", activityInfo.getWorkflowId(), activityInfo.getActivityId(), logPath, e);
                            return Collections.<String>emptyList();
                        }
                    } else {
                        return List.of(logPath);
                    }
                })
                .flatMap(List::stream)
                .toList();
    }

    private String findLogFileForDate(String logsRootPath, String date) {
        ActivityInfo activityInfo = Activity.getExecutionContext().getInfo();
        logger.info("WorkflowId {} ActivityId {} - Finding HL7 log file for date {} in root path {}", activityInfo.getWorkflowId(), activityInfo.getActivityId(), date, logsRootPath);
        try {
            return Files.walk(Path.of(logsRootPath))
                    .filter(Files::isRegularFile)
                    .filter(path -> path.getFileName().toString().contains(date))
                    .map(Path::toString)
                    .filter(s -> s.endsWith(".log"))
                    .findFirst()
                    .orElseThrow(() -> ApplicationFailure.newFailure("No filename contained date " + date + " in " + logsRootPath, "type"));
        } catch (IOException e) {
            throw Activity.wrap(e);
        }
    }
}
