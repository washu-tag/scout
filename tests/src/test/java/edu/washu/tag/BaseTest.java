package edu.washu.tag;

import static io.temporal.api.enums.v1.WorkflowExecutionStatus.WORKFLOW_EXECUTION_STATUS_COMPLETED;
import static io.temporal.api.enums.v1.WorkflowExecutionStatus.WORKFLOW_EXECUTION_STATUS_CONTINUED_AS_NEW;
import static org.awaitility.Awaitility.await;

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
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.testng.annotations.BeforeSuite;

public class BaseTest {

    private static final Logger log = LoggerFactory.getLogger(BaseTest.class);
    private static final List<WorkflowExecutionStatus> successfulWorkflowStatuses = Arrays.asList(
        WORKFLOW_EXECUTION_STATUS_CONTINUED_AS_NEW,
        WORKFLOW_EXECUTION_STATUS_COMPLETED
    );
    private static final String NAMESPACE = "default";
    protected TestConfig config;
    protected String ingestWorkflowId;
    protected List<String> ingestToDeltaLakeWorkflows = new ArrayList<>();

    public BaseTest() {
        config = TestConfig.instance;
    }

    @BeforeSuite
    public void launchIngestion() {
        final WorkflowServiceStubsOptions serviceStubOptions = WorkflowServiceStubsOptions.newBuilder()
            .setTarget("temporal-frontend.temporal.svc:7233")
            .build();

        final WorkflowServiceStubs workflowServiceStubs = WorkflowServiceStubs.newServiceStubs(serviceStubOptions);

        final WorkflowClient client = WorkflowClient.newInstance(workflowServiceStubs);

        final WorkflowStub workflow = client.newUntypedWorkflowStub(
            "IngestHl7LogWorkflow",
            WorkflowOptions.newBuilder()
                .setTaskQueue("ingest-hl7-log")
                .build()
        );

        workflow.start(
            new IngestJobInput()
                .setDeltaLakePath("s3://lake/orchestration/delta/test_data")
                .setHl7OutputPath("s3://lake/orchestration/hl7")
                .setScratchSpaceRootPath("s3://lake/orchestration/scratch")
                .setLogsRootPath("/data/hl7")
        );
        workflow.getResult(Map.class);
        final WorkflowExecution workflowExecution = workflow.getExecution();
        ingestWorkflowId = workflowExecution.getWorkflowId();
        waitForWorkflowChain(workflowServiceStubs, workflowExecution);
        log.info("Ingest complete with ingest workflowId {} and child delta lake ingest workflowIds {}", ingestWorkflowId, ingestToDeltaLakeWorkflows);
    }

    private DescribeWorkflowExecutionResponse waitForWorkflowInStatus(WorkflowServiceStubs workflowServiceStubs,
        WorkflowExecution workflowExecution, List<WorkflowExecutionStatus> permittedStatuses) {
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
            Collections.singletonList(WORKFLOW_EXECUTION_STATUS_COMPLETED)
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
