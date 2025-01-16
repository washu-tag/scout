package edu.washu.tag.temporal.workflow;

import edu.washu.tag.temporal.activity.IngestHl7FilesToDeltaLakeActivity;
import edu.washu.tag.temporal.activity.SplitHl7LogActivity;
import edu.washu.tag.temporal.model.FindHl7LogFileInput;
import edu.washu.tag.temporal.model.FindHl7LogFileOutput;
import edu.washu.tag.temporal.model.IngestHl7FilesToDeltaLakeInput;
import edu.washu.tag.temporal.model.IngestHl7FilesToDeltaLakeOutput;
import edu.washu.tag.temporal.model.IngestHl7LogWorkflowInput;
import edu.washu.tag.temporal.model.IngestHl7LogWorkflowOutput;
import edu.washu.tag.temporal.model.SplitHl7LogActivityInput;
import edu.washu.tag.temporal.model.SplitHl7LogActivityOutput;
import edu.washu.tag.temporal.model.TransformSplitHl7LogInput;
import edu.washu.tag.temporal.model.TransformSplitHl7LogOutput;
import io.temporal.activity.ActivityOptions;
import io.temporal.spring.boot.WorkflowImpl;
import io.temporal.workflow.Workflow;
import io.temporal.workflow.WorkflowInfo;

import java.time.Duration;
import java.util.ArrayList;
import java.util.List;

@WorkflowImpl(taskQueues = "ingest-hl7-log")
public class IngestHl7LogWorkflowImpl implements IngestHl7LogWorkflow {
    private final SplitHl7LogActivity hl7LogActivity =
            Workflow.newActivityStub(SplitHl7LogActivity.class,
                    ActivityOptions.newBuilder()
                            .setStartToCloseTimeout(Duration.ofSeconds(10))
                            .build());
    private final IngestHl7FilesToDeltaLakeActivity ingestActivity =
            Workflow.newActivityStub(IngestHl7FilesToDeltaLakeActivity.class,
                    ActivityOptions.newBuilder()
                            .setStartToCloseTimeout(Duration.ofSeconds(30))
                            .build());

    @Override
    public IngestHl7LogWorkflowOutput ingestHl7Log(IngestHl7LogWorkflowInput input) {
        WorkflowInfo workflowInfo = Workflow.getInfo();

        String scratchDir = input.scratchSpaceRootPath() + (input.scratchSpaceRootPath().endsWith("/") ? "" : "/") + workflowInfo.getWorkflowId();

        // Find log file by date
        FindHl7LogFileInput findHl7LogFileInput = new FindHl7LogFileInput(input.date(), input.logsRootPath());
        FindHl7LogFileOutput findHl7LogFileOutput = hl7LogActivity.findHl7LogFile(findHl7LogFileInput);

        // Split log file
        String splitLogFileOutputPath = scratchDir + "/split";
        SplitHl7LogActivityInput splitHl7LogInput = new SplitHl7LogActivityInput(findHl7LogFileOutput.logFileAbsPath(), splitLogFileOutputPath);
        SplitHl7LogActivityOutput splitHl7LogOutput = hl7LogActivity.splitHl7Log(splitHl7LogInput);

        // Fan out
        String hl7RootPath = scratchDir + "/hl7";
        final List<String> hl7RelativePaths = new ArrayList<>();
        for (String splitLogFileRelativePath : splitHl7LogOutput.relativePaths()) {
            // Transform split log file into HL7
            String splitLogFilePath = splitHl7LogOutput.rootPath() + "/" + splitLogFileRelativePath;
            TransformSplitHl7LogInput transformSplitHl7LogInput = new TransformSplitHl7LogInput(splitLogFilePath, hl7RootPath);
            TransformSplitHl7LogOutput transformSplitHl7LogOutput = hl7LogActivity.transformSplitHl7Log(transformSplitHl7LogInput);
            hl7RelativePaths.add(transformSplitHl7LogOutput.relativePath());
        }

        // Ingest HL7 into delta lake
        IngestHl7FilesToDeltaLakeInput ingestHl7FilesToDeltaLakeInput = new IngestHl7FilesToDeltaLakeInput(input.outputRootPath(), hl7RelativePaths);
        IngestHl7FilesToDeltaLakeOutput ingestHl7LogWorkflowOutput = ingestActivity.ingestHl7FilesToDeltaLake(ingestHl7FilesToDeltaLakeInput);

        return new IngestHl7LogWorkflowOutput();
    }
}
