package edu.washu.tag;

import static io.temporal.api.enums.v1.WorkflowExecutionStatus.WORKFLOW_EXECUTION_STATUS_COMPLETED;
import static io.temporal.api.enums.v1.WorkflowExecutionStatus.WORKFLOW_EXECUTION_STATUS_CONTINUED_AS_NEW;
import static org.awaitility.Awaitility.await;

import edu.washu.tag.model.IngestJobOutput;
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
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Set;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.testng.annotations.BeforeSuite;

public class BaseTest {

    private static final Logger log = LoggerFactory.getLogger(BaseTest.class);
    private static final Set<WorkflowExecutionStatus> successfulWorkflowStatuses = Set.of(
        WORKFLOW_EXECUTION_STATUS_CONTINUED_AS_NEW,
        WORKFLOW_EXECUTION_STATUS_COMPLETED
    );
    private static final String NAMESPACE = "default";
    protected static String ingestWorkflowId;
    protected static List<String> ingestToDeltaLakeWorkflows = new ArrayList<>();
    protected TestConfig config;

    public BaseTest() {
        config = TestConfig.instance;
    }

    @BeforeSuite
    public void launchIngestion() {
        final WorkflowServiceStubsOptions serviceStubOptions = WorkflowServiceStubsOptions.newBuilder()
            .setTarget(config.getTemporalConfig().getTemporalUrl())
            .build();

        final WorkflowServiceStubs workflowServiceStubs = WorkflowServiceStubs.newServiceStubs(serviceStubOptions);

        final WorkflowClient client = WorkflowClient.newInstance(workflowServiceStubs);

        final WorkflowStub workflow = client.newUntypedWorkflowStub(
            "IngestHl7LogWorkflow",
            WorkflowOptions.newBuilder()
                .setTaskQueue("ingest-hl7-log")
                .build()
        );

        workflow.start(config.getTemporalConfig().getIngestJobInput());
        workflow.getResult(IngestJobOutput.class); // block on initial workflow done to reduce chatter below
        final WorkflowExecution workflowExecution = workflow.getExecution();
        ingestWorkflowId = workflowExecution.getWorkflowId();
        waitForWorkflowChain(workflowServiceStubs, workflowExecution);
        log.info("Ingest complete with ingest workflowId {} and child delta lake ingest workflowIds {}", ingestWorkflowId, ingestToDeltaLakeWorkflows);
    }

    private DescribeWorkflowExecutionResponse waitForWorkflowInStatus(WorkflowServiceStubs workflowServiceStubs,
        WorkflowExecution workflowExecution, Set<WorkflowExecutionStatus> permittedStatuses) {
        log.info("Waiting for workflow with ID {} to be in one of the following statuses: {}", workflowExecution.getWorkflowId(), permittedStatuses);
        return await().atMost(Duration.ofMinutes(5)).until(
            () -> workflowServiceStubs.blockingStub().describeWorkflowExecution(
                DescribeWorkflowExecutionRequest.newBuilder()
                    .setNamespace(NAMESPACE)
                    .setExecution(workflowExecution)
                    .build()
            ),
            (response) -> permittedStatuses.contains(response.getWorkflowExecutionInfo().getStatus())
        );
    }

    private void waitForWorkflowChain(WorkflowServiceStubs workflowServiceStubs, WorkflowExecution workflowExecution) {
        final DescribeWorkflowExecutionResponse completeOrContinuedResponse = waitForWorkflowInStatus(
            workflowServiceStubs, workflowExecution, successfulWorkflowStatuses
        );

        final PendingChildExecutionInfo ingestToDeltaLakeWorkflow = completeOrContinuedResponse.getPendingChildren(0);
        waitForWorkflowInStatus(
            workflowServiceStubs,
            WorkflowExecution.newBuilder()
                .setWorkflowId(ingestToDeltaLakeWorkflow.getWorkflowId())
                .setRunId(ingestToDeltaLakeWorkflow.getRunId())
                .build(),
            Collections.singleton(WORKFLOW_EXECUTION_STATUS_COMPLETED)
        );
        ingestToDeltaLakeWorkflows.add(ingestToDeltaLakeWorkflow.getWorkflowId());

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
                workflowServiceStubs,
                WorkflowExecution.newBuilder()
                    .setWorkflowId(workflowExecution.getWorkflowId())
                    .setRunId(continuedRunId)
                    .build()
            );
        }
    }

}
