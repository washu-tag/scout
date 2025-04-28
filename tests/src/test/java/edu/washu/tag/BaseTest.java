package edu.washu.tag;

import edu.washu.tag.model.IngestJobInput;
import io.temporal.api.common.v1.WorkflowExecution;
import io.temporal.client.WorkflowClient;
import io.temporal.client.WorkflowOptions;
import io.temporal.client.WorkflowStub;
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

        final WorkflowClient client = WorkflowClient.newInstance(
            WorkflowServiceStubs.newServiceStubs(serviceStubOptions)
        );

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
        final Map<String, Object> result = workflow.getResult(Map.class);
        for (Map.Entry<String, Object> entry : result.entrySet()) {
            log.error("Map entry {} -> {}", entry.getKey(), entry.getValue());
        }
        final WorkflowExecution workflowExecution = workflow.getExecution();
        log.error("WORKFLOWID {}", workflowExecution.getWorkflowId());
        log.error("RUNID {}", workflowExecution.getRunId());
    }

}
