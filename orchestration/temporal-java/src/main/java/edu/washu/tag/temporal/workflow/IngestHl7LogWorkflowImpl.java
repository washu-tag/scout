package edu.washu.tag.temporal.workflow;

import edu.washu.tag.temporal.activity.IngestHl7FilesToDeltaLakeActivity;
import edu.washu.tag.temporal.activity.SplitHl7LogActivity;
import edu.washu.tag.temporal.model.IngestHl7FilesToDeltaLakeInput;
import edu.washu.tag.temporal.model.IngestHl7LogWorkflowInput;
import edu.washu.tag.temporal.model.IngestHl7LogWorkflowOutput;
import edu.washu.tag.temporal.model.SplitHl7LogActivityInput;
import edu.washu.tag.temporal.model.TransformSplitHl7LogInput;
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

        var scratchDir = input.scratchSpaceRootPath() + "/" + workflowInfo.getWorkflowId();

        // Split log file
        var splitLogFileOutputPath = scratchDir + "/split";
        var splitHl7LogInput = new SplitHl7LogActivityInput(input.logPath(), splitLogFileOutputPath);
        var splitHl7LogOutput = hl7LogActivity.splitHl7Log(splitHl7LogInput);

        // Fan out
        var hl7RootPath = scratchDir + "/hl7";
        final List<String> hl7RelativePaths = new ArrayList<>();
        for (var splitLogFileRelativePath : splitHl7LogOutput.relativePaths()) {
            // Transform split log file into HL7
            var splitLogFilePath = splitHl7LogOutput.rootPath() + "/" + splitLogFileRelativePath;
            var transformSplitHl7LogInput = new TransformSplitHl7LogInput(splitLogFilePath, hl7RootPath);
            var transformSplitHl7LogOutput = hl7LogActivity.transformSplitHl7Log(transformSplitHl7LogInput);
            hl7RelativePaths.add(transformSplitHl7LogOutput.relativePath());
        }

        // Ingest HL7 into delta lake
        var ingestHl7FilesToDeltaLakeInput = new IngestHl7FilesToDeltaLakeInput(input.outputRootPath(), hl7RelativePaths);
        var ingestHl7LogWorkflowOutput = ingestActivity.ingestHl7FilesToDeltaLake(ingestHl7FilesToDeltaLakeInput);

        return new IngestHl7LogWorkflowOutput();
    }
}
