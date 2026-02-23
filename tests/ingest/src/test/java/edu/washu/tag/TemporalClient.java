package edu.washu.tag;

import static io.temporal.api.enums.v1.WorkflowExecutionStatus.WORKFLOW_EXECUTION_STATUS_COMPLETED;
import static io.temporal.api.enums.v1.WorkflowExecutionStatus.WORKFLOW_EXECUTION_STATUS_CONTINUED_AS_NEW;
import static io.temporal.api.enums.v1.WorkflowExecutionStatus.WORKFLOW_EXECUTION_STATUS_FAILED;
import static org.awaitility.Awaitility.await;

import edu.washu.tag.model.IngestJobDetails;
import edu.washu.tag.model.IngestJobInput;
import io.temporal.api.common.v1.WorkflowExecution;
import io.temporal.api.enums.v1.WorkflowExecutionStatus;
import io.temporal.api.workflow.v1.PendingChildExecutionInfo;
import io.temporal.api.workflowservice.v1.DescribeWorkflowExecutionRequest;
import io.temporal.api.workflowservice.v1.DescribeWorkflowExecutionResponse;
import io.temporal.api.workflowservice.v1.GetWorkflowExecutionHistoryRequest;
import io.temporal.client.WorkflowClient;
import io.temporal.client.WorkflowOptions;
import io.temporal.client.WorkflowStub;
import io.temporal.common.WorkflowExecutionHistory;
import io.temporal.serviceclient.WorkflowServiceStubs;
import io.temporal.serviceclient.WorkflowServiceStubsOptions;
import java.time.Duration;
import java.util.Collections;
import java.util.Set;
import java.util.concurrent.Callable;
import java.util.concurrent.atomic.AtomicReference;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

public class TemporalClient {

    private static final Logger log = LoggerFactory.getLogger(TemporalClient.class);
    private static final String NAMESPACE = "default";
    private static final Set<WorkflowExecutionStatus> successfulWorkflowStatuses = Set.of(
        WORKFLOW_EXECUTION_STATUS_CONTINUED_AS_NEW,
        WORKFLOW_EXECUTION_STATUS_COMPLETED
    );
    private final WorkflowServiceStubs workflowServiceStubs;
    private final WorkflowClient client;

    public TemporalClient(TestConfig config) {
        final WorkflowServiceStubsOptions serviceStubOptions = WorkflowServiceStubsOptions.newBuilder()
            .setTarget(config.getTemporalConfig().getTemporalUrl())
            .build();
        workflowServiceStubs = WorkflowServiceStubs.newServiceStubs(serviceStubOptions);
        client = WorkflowClient.newInstance(workflowServiceStubs);
    }

    public IngestJobDetails launchIngest(IngestJobInput ingestJobInput, boolean expectSuccess) {
        final WorkflowStub workflow = getIngestStub();
        workflow.start(ingestJobInput);
        final WorkflowExecution workflowExecution = workflow.getExecution();
        final IngestJobDetails ingestJobDetails = new IngestJobDetails();
        ingestJobDetails
            .setIngestWorkflowId(workflowExecution.getWorkflowId())
            .setWorkflowExecution(workflowExecution);
        waitForWorkflowChain(ingestJobDetails, workflowExecution, expectSuccess);
        return ingestJobDetails;
    }

    private WorkflowStub getIngestStub() {
        return client.newUntypedWorkflowStub(
            "IngestHl7LogWorkflow",
            WorkflowOptions.newBuilder()
                .setTaskQueue("ingest-hl7-log")
                .build()
        );
    }

    private DescribeWorkflowExecutionResponse waitForWorkflowInStatus(WorkflowExecution workflowExecution, Set<WorkflowExecutionStatus> permittedStatuses) {
        log.info("Waiting for workflow with ID {} to be in one of the following statuses: {}", workflowExecution.getWorkflowId(), permittedStatuses);
        final AtomicReference<DescribeWorkflowExecutionResponse> currentWorkflow = new AtomicReference<>();
        final AtomicReference<WorkflowExecutionStatus> currentStatus = new AtomicReference<>();
        final Callable<Boolean> failFast = failFastForStatus(permittedStatuses, currentStatus);

        return await()
            .pollInterval(Duration.ofMillis(500))
            .failFast("Temporal workflow failed", failFast)
            .atMost(Duration.ofMinutes(5))
            .until(
                () -> {
                    currentWorkflow.set(
                        workflowServiceStubs.blockingStub().describeWorkflowExecution(
                            DescribeWorkflowExecutionRequest.newBuilder()
                                .setNamespace(NAMESPACE)
                                .setExecution(workflowExecution)
                                .build()
                        )
                    );
                    currentStatus.set(currentWorkflow.get().getWorkflowExecutionInfo().getStatus());
                    log.info("Current workflow state: {}", currentStatus.get());
                    return currentWorkflow;
                },
                (ignored) -> permittedStatuses.contains(currentStatus.get())
            ).get();
    }

    private void waitForWorkflowChain(IngestJobDetails ingestJobDetails, WorkflowExecution workflowExecution, boolean expectSuccess) {
        if (!expectSuccess) {
            waitForWorkflowInStatus(
                workflowExecution, Collections.singleton(WORKFLOW_EXECUTION_STATUS_FAILED)
            );
            return;
        }

        final DescribeWorkflowExecutionResponse completeOrContinuedResponse = waitForWorkflowInStatus(
            workflowExecution, successfulWorkflowStatuses
        );

        final PendingChildExecutionInfo ingestToDeltaLakeWorkflow = completeOrContinuedResponse.getPendingChildren(0);
        waitForWorkflowInStatus(
            WorkflowExecution.newBuilder()
                .setWorkflowId(ingestToDeltaLakeWorkflow.getWorkflowId())
                .setRunId(ingestToDeltaLakeWorkflow.getRunId())
                .build(),
            Collections.singleton(WORKFLOW_EXECUTION_STATUS_COMPLETED)
        );
        ingestJobDetails.getIngestToDeltaLakeWorkflows().add(ingestToDeltaLakeWorkflow.getWorkflowId());

        if (completeOrContinuedResponse.getWorkflowExecutionInfo().getStatus() == WORKFLOW_EXECUTION_STATUS_CONTINUED_AS_NEW) {
            log.info("Workflow was continued as new, querying for continued workflow...");
            final GetWorkflowExecutionHistoryRequest historyRequest = GetWorkflowExecutionHistoryRequest.newBuilder()
                .setNamespace(NAMESPACE)
                .setExecution(workflowExecution)
                .build();
            final WorkflowExecutionHistory workHistoryResponse = new WorkflowExecutionHistory(
                workflowServiceStubs.blockingStub().getWorkflowExecutionHistory(historyRequest).getHistory()
            );
            final String continuedRunId = workHistoryResponse
                .getLastEvent()
                .getWorkflowExecutionContinuedAsNewEventAttributes()
                .getNewExecutionRunId();
            log.info("Workflow continued as new found with new runId: {}", continuedRunId);
            waitForWorkflowChain(
                ingestJobDetails,
                WorkflowExecution.newBuilder()
                    .setWorkflowId(ingestJobDetails.getIngestWorkflowId())
                    .setRunId(continuedRunId)
                    .build(),
                true
            );
        } else {
            log.info(
                "Ingest complete with ingest workflowId {} and child delta lake ingest workflowIds {}",
                ingestJobDetails.getIngestWorkflowId(),
                ingestJobDetails.getIngestToDeltaLakeWorkflows()
            );
            ingestJobDetails.setWorkflowExecutionStatus(completeOrContinuedResponse.getWorkflowExecutionInfo().getStatus());
        }
    }

    private Callable<Boolean> failFastForStatus(Set<WorkflowExecutionStatus> permittedStatuses, AtomicReference<WorkflowExecutionStatus> currentStatus) {
        if (permittedStatuses.contains(WORKFLOW_EXECUTION_STATUS_FAILED)) {
            return () -> false;
        } else {
            return () -> WORKFLOW_EXECUTION_STATUS_FAILED.equals(currentStatus.get());
        }
    }

}
