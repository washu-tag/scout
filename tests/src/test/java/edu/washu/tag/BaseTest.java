package edu.washu.tag;

import edu.washu.tag.model.IngestJobInput;
import io.temporal.api.common.v1.WorkflowExecution;
import io.temporal.client.WorkflowClient;
import io.temporal.client.WorkflowOptions;
import io.temporal.client.WorkflowStub;
import io.temporal.serviceclient.WorkflowServiceStubs;
import io.temporal.serviceclient.WorkflowServiceStubsOptions;
import java.util.Map;
import org.apache.log4j.Logger;
import org.testng.annotations.BeforeSuite;

public class BaseTest {

    private static final Logger log = Logger.getLogger(BaseTest.class);
    protected TestConfig config;

    public BaseTest() {
        config = TestConfig.instance;
    }

    @BeforeSuite
    public void launchIngestion() {
        final WorkflowServiceStubsOptions serviceStubOptions = WorkflowServiceStubsOptions.newBuilder()
            .setTarget("temporal-frontend.temporal.svc:7233")
            .build();

        final WorkflowClient client = WorkflowClient.newInstance(
            WorkflowServiceStubs.newServiceStubs(serviceStubOptions)
        );

        final WorkflowStub workflow = client.newUntypedWorkflowStub(
            "IngestHl7LogWorkflow",
            WorkflowOptions.newBuilder()
                .setWorkflowId()
                .setWorkflowId("IngestHl7LogWorkflow")
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
        log.fatal("WORKFLOWID " + workflowExecution.getWorkflowId());
        log.fatal("RUNID " + workflowExecution.getRunId());
    }

}
