package edu.washu.tag.extractor.hl7log.workflow;

import static edu.washu.tag.extractor.hl7log.util.Constants.BUILD_MANIFEST_QUEUE;
import static edu.washu.tag.extractor.hl7log.util.Constants.INGEST_DELTA_LAKE_QUEUE;
import static edu.washu.tag.extractor.hl7log.util.Constants.PYTHON_ACTIVITY;
import static edu.washu.tag.extractor.hl7log.util.Constants.REFRESH_VIEWS_QUEUE;

import edu.washu.tag.extractor.hl7log.activity.FindHl7Files;
import edu.washu.tag.extractor.hl7log.model.FindHl7FilesInput;
import edu.washu.tag.extractor.hl7log.model.FindHl7FilesOutput;
import edu.washu.tag.extractor.hl7log.model.IngestHl7FilesToDeltaLakeActivityInput;
import edu.washu.tag.extractor.hl7log.model.IngestHl7FilesToDeltaLakeInput;
import edu.washu.tag.extractor.hl7log.model.IngestHl7FilesToDeltaLakeOutput;
import edu.washu.tag.extractor.hl7log.model.RefreshIngestDbViewsInput;
import edu.washu.tag.extractor.hl7log.model.WriteHl7FilesErrorStatusToDbInput;
import edu.washu.tag.extractor.hl7log.util.DefaultArgs;
import io.temporal.activity.ActivityOptions;
import io.temporal.api.common.v1.WorkflowExecution;
import io.temporal.api.enums.v1.ParentClosePolicy;
import io.temporal.common.RetryOptions;
import io.temporal.failure.ActivityFailure;
import io.temporal.spring.boot.WorkflowImpl;
import io.temporal.workflow.ActivityStub;
import io.temporal.workflow.Async;
import io.temporal.workflow.ChildWorkflowOptions;
import io.temporal.workflow.Promise;
import io.temporal.workflow.Workflow;
import io.temporal.workflow.WorkflowInfo;
import java.time.Duration;
import org.slf4j.Logger;

@WorkflowImpl(taskQueues = INGEST_DELTA_LAKE_QUEUE)
public class IngestHl7ToDeltaLakeWorkflowImpl implements IngestHl7ToDeltaLakeWorkflow {
    private static final Logger logger = Workflow.getLogger(IngestHl7ToDeltaLakeWorkflowImpl.class);

    private final FindHl7Files findHl7Files =
        Workflow.newActivityStub(FindHl7Files.class,
            ActivityOptions.newBuilder()
                .setTaskQueue(BUILD_MANIFEST_QUEUE)
                .setStartToCloseTimeout(Duration.ofMinutes(5))
                .setRetryOptions(RetryOptions.newBuilder()
                    .setMaximumAttempts(3)
                    .build())
                .build()
        );

    private final RefreshIngestDbViewsWorkflow refreshIngestDbViewsWorkflow =
        Workflow.newChildWorkflowStub(
            RefreshIngestDbViewsWorkflow.class,
            ChildWorkflowOptions.newBuilder()
                .setTaskQueue(REFRESH_VIEWS_QUEUE)
                .setParentClosePolicy(ParentClosePolicy.PARENT_CLOSE_POLICY_ABANDON)
                .build()
        );


    @Override
    public IngestHl7FilesToDeltaLakeOutput ingestHl7FileToDeltaLake(IngestHl7FilesToDeltaLakeInput input) {
        WorkflowInfo workflowInfo = Workflow.getInfo();

        final ActivityStub ingestActivity =
            Workflow.newUntypedActivityStub(
                ActivityOptions.newBuilder()
                    .setTaskQueue(INGEST_DELTA_LAKE_QUEUE)
                    .setStartToCloseTimeout(Duration.ofMinutes(DefaultArgs.getDeltaIngestTimeout(input.deltaIngestTimeout())))
                    .setRetryOptions(RetryOptions.newBuilder()
                        .setMaximumInterval(Duration.ofMinutes(5))
                        .setMaximumAttempts(5)
                        .build())
                    .build());

        String hl7ManifestFilePath = input.hl7ManifestFilePath();
        if (input.hl7ManifestFilePath() == null || input.hl7ManifestFilePath().isEmpty()) {
            // Find HL7 files and build manifest
            logger.info("WorkflowId {} - Finding HL7 files using root path: {}", workflowInfo.getWorkflowId(), input.hl7RootPath());
            if ((input.hl7RootPath() == null || input.hl7RootPath().isEmpty()) ||
                (input.scratchSpaceRootPath() == null || input.scratchSpaceRootPath().isEmpty())) {
                throw new IllegalArgumentException("hl7RootPath and scratchSpaceRootPath must be provided if hl7ManifestFilePath is not provided");
            }
            String scratchDir = input.scratchSpaceRootPath() + (input.scratchSpaceRootPath().endsWith("/") ? "" : "/") + workflowInfo.getWorkflowId();
            FindHl7FilesOutput output = findHl7Files.findHl7FilesAndWriteManifest(new FindHl7FilesInput(input.hl7RootPath(), scratchDir));
            hl7ManifestFilePath = output.hl7ManifestFilePath();
            logger.info("WorkflowId {} - Using manifest file path: {}", workflowInfo.getWorkflowId(), hl7ManifestFilePath);
        } else {
            logger.info("WorkflowId {} - Using provided HL7 manifest file path: {}", workflowInfo.getWorkflowId(), input.hl7ManifestFilePath());
        }

        // Validate input
        String reportTableName = DefaultArgs.getReportTableName(input.reportTableName());
        String modalityMapPath = DefaultArgs.getModalityMapPath(input.modalityMapPath());
        if (modalityMapPath == null || modalityMapPath.isEmpty()) {
            throw new IllegalArgumentException("modalityMapPath must be provided");
        }

        // Ingest HL7 into delta lake
        logger.info("WorkflowId {} - Launching activity to ingest HL7 files", workflowInfo.getWorkflowId());
        Promise<IngestHl7FilesToDeltaLakeOutput> ingestHl7Promise = ingestActivity.executeAsync(
            PYTHON_ACTIVITY,
            IngestHl7FilesToDeltaLakeOutput.class,
            new IngestHl7FilesToDeltaLakeActivityInput(reportTableName, modalityMapPath, hl7ManifestFilePath)
        );

        IngestHl7FilesToDeltaLakeOutput ingestHl7Output;
        try {
            ingestHl7Output = ingestHl7Promise.get();
        } catch (ActivityFailure e) {
            logger.warn("WorkflowId {} - Ingest HL7 files activity failed. Marking all HL7 files as \"failed\" status.", workflowInfo.getWorkflowId());
            try {
                findHl7Files.writeHl7FilesErrorStatusToDb(
                    new WriteHl7FilesErrorStatusToDbInput(hl7ManifestFilePath, "Error ingesting HL7 to delta lake: " + e.getMessage())
                );
            } catch (Exception e2) {
                logger.error("WorkflowId {} - Error marking HL7 files as \"failed\" status", workflowInfo.getWorkflowId(), e2);
                throw e2;
            }
            throw e;
        }

        logger.info("WorkflowId {} - Activity complete, ingested {} HL7 files",
            workflowInfo.getWorkflowId(), ingestHl7Output.numHl7Ingested());

        // Launch refresh views workflow
        Async.function(
            refreshIngestDbViewsWorkflow::refreshIngestDbViews,
            new RefreshIngestDbViewsInput()
        );
        // Wait for the refresh views workflow to start
        Promise<WorkflowExecution> refreshViewsWorkflowPromise = Workflow.getWorkflowExecution(refreshIngestDbViewsWorkflow);
        WorkflowExecution refreshViewsWorkflow = refreshViewsWorkflowPromise.get();
        logger.info("WorkflowId {} - Launched refresh views workflow {}",
            workflowInfo.getWorkflowId(), refreshViewsWorkflow.getWorkflowId());

        return ingestHl7Output;
    }
}
