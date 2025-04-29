package edu.washu.tag;

import edu.washu.tag.model.IngestJobInput;
import io.temporal.api.common.v1.WorkflowExecution;
import io.temporal.api.workflowservice.v1.DescribeWorkflowExecutionRequest;
import io.temporal.api.workflowservice.v1.DescribeWorkflowExecutionResponse;
import io.temporal.api.workflowservice.v1.GetWorkflowExecutionHistoryRequest;
import io.temporal.api.workflowservice.v1.WorkflowServiceGrpc.WorkflowServiceStub;
import io.temporal.client.WorkflowClient;
import io.temporal.client.WorkflowOptions;
import io.temporal.client.WorkflowStub;
import io.temporal.common.WorkflowExecutionHistory;
import io.temporal.serviceclient.WorkflowServiceStubs;
import io.temporal.serviceclient.WorkflowServiceStubsOptions;
import java.util.Map;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.testng.annotations.BeforeSuite;

public class BaseTest {

    private static final Logger log = LoggerFactory.getLogger(BaseTest.class);
    protected TestConfig config;

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
        final String workflowId = workflowExecution.getWorkflowId();
        log.error("WORKFLOWID {}", workflowId);
        log.error("RUNID {}", workflowExecution.getRunId());

        for (int i = 0; i < 11; i++) {
            final DescribeWorkflowExecutionResponse describeWorkflowExecutionResponse = workflowServiceStubs.blockingStub().describeWorkflowExecution(
                DescribeWorkflowExecutionRequest.newBuilder()
                    .setNamespace("default")
                    .setExecution(workflowExecution).build()
            );
            log.error("Pending activity count: {}", describeWorkflowExecutionResponse.getPendingActivitiesCount());
        }












        GetWorkflowExecutionHistoryRequest historyRequest = GetWorkflowExecutionHistoryRequest.newBuilder()
            .setNamespace("default")
            .setExecution(workflowExecution)
            .build();
        new WorkflowExecutionHistory(workflowServiceStubs.blockingStub().getWorkflowExecutionHistory(historyRequest).getHistory())
    }

}
