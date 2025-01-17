package edu.washu.tag.temporal.workflow;

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
import io.temporal.workflow.ActivityStub;
import io.temporal.workflow.Async;
import io.temporal.workflow.Promise;
import io.temporal.workflow.Workflow;
import io.temporal.workflow.WorkflowInfo;
import org.slf4j.Logger;

import java.time.Duration;
import java.util.ArrayList;
import java.util.List;

@WorkflowImpl(taskQueues = "ingest-hl7-log")
public class IngestHl7LogWorkflowImpl implements IngestHl7LogWorkflow {
    private static final Logger logger = Workflow.getLogger(IngestHl7LogWorkflowImpl.class);

    private final SplitHl7LogActivity hl7LogActivity =
            Workflow.newActivityStub(SplitHl7LogActivity.class,
                    ActivityOptions.newBuilder()
                            .setStartToCloseTimeout(Duration.ofSeconds(10))
                            .build());

    private static final String INGEST_ACTIVITY_NAME = "ingest_hl7_files_to_delta_lake_activity";
    private final ActivityStub ingestActivity =
        Workflow.newUntypedActivityStub(
            ActivityOptions.newBuilder()
                    .setTaskQueue("ingest-hl7-delta-lake")
                    .setStartToCloseTimeout(Duration.ofSeconds(30))
                    .build());

    @Override
    public IngestHl7LogWorkflowOutput ingestHl7Log(IngestHl7LogWorkflowInput input) {
        WorkflowInfo workflowInfo = Workflow.getInfo();
        logger.info("Ingesting HL7 log for workflowId {}", workflowInfo.getWorkflowId());

        String scratchDir = input.scratchSpaceRootPath() + (input.scratchSpaceRootPath().endsWith("/") ? "" : "/") + workflowInfo.getWorkflowId();

        // Find log file by date
        String date = input.date().replace("-", "");
        FindHl7LogFileInput findHl7LogFileInput = new FindHl7LogFileInput(date, input.logsRootPath());
        FindHl7LogFileOutput findHl7LogFileOutput = hl7LogActivity.findHl7LogFile(findHl7LogFileInput);

        // Split log file
        String splitLogFileOutputPath = scratchDir + "/split";
        SplitHl7LogActivityInput splitHl7LogInput = new SplitHl7LogActivityInput(findHl7LogFileOutput.logFileAbsPath(), splitLogFileOutputPath);
        SplitHl7LogActivityOutput splitHl7LogOutput = hl7LogActivity.splitHl7Log(splitHl7LogInput);

        // Fan out
        String hl7RootPath = input.hl7OutputPath().endsWith("/") ? input.hl7OutputPath().substring(0, input.hl7OutputPath().length() - 1) : input.hl7OutputPath();
        List<Promise<TransformSplitHl7LogOutput>> transformSplitHl7LogOutputPromises = new ArrayList<>();
        for (String splitLogFileRelativePath : splitHl7LogOutput.relativePaths()) {
            // Async call to transform split log file into HL7
            String splitLogFilePath = splitHl7LogOutput.rootPath() + "/" + splitLogFileRelativePath;
            TransformSplitHl7LogInput transformSplitHl7LogInput = new TransformSplitHl7LogInput(splitLogFilePath, hl7RootPath);
            Promise<TransformSplitHl7LogOutput> transformSplitHl7LogOutputPromise =
                    Async.function(hl7LogActivity::transformSplitHl7Log, transformSplitHl7LogInput);
            transformSplitHl7LogOutputPromises.add(transformSplitHl7LogOutputPromise);
        }
        // Collect async results
        final List<String> hl7AbsolutePaths = transformSplitHl7LogOutputPromises.stream()
                .map(Promise::get)
                .map(TransformSplitHl7LogOutput::relativePath)
                .map(relativePath -> hl7RootPath + "/" + relativePath)
                .toList();

        // Ingest HL7 into delta lake
        // We execute the activity using the untyped stub because the activity is implemented in a different language
        IngestHl7FilesToDeltaLakeOutput ingestHl7LogWorkflowOutput = ingestActivity.execute(
                INGEST_ACTIVITY_NAME,
                IngestHl7FilesToDeltaLakeOutput.class,
                new IngestHl7FilesToDeltaLakeInput(input.deltaLakePath(), hl7AbsolutePaths)
        );

        return new IngestHl7LogWorkflowOutput();
    }
}
