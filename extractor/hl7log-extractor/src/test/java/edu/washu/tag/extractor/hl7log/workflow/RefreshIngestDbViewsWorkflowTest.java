package edu.washu.tag.extractor.hl7log.workflow;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;

import edu.washu.tag.extractor.hl7log.activity.CheckRunningTransformersActivity;
import edu.washu.tag.extractor.hl7log.activity.RefreshIngestDbViewsActivity;
import edu.washu.tag.extractor.hl7log.model.RefreshIngestDbViewsOutput;
import edu.washu.tag.extractor.hl7log.model.ViewRefreshStatus;
import io.temporal.client.WorkflowClient;
import io.temporal.client.WorkflowOptions;
import io.temporal.testing.TestWorkflowEnvironment;
import io.temporal.testing.TestWorkflowExtension;
import io.temporal.worker.Worker;
import java.time.Duration;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.RegisterExtension;

/**
 * Unit tests for RefreshIngestDbViewsWorkflow.
 */
class RefreshIngestDbViewsWorkflowTest {

    @RegisterExtension
    public static final TestWorkflowExtension testWorkflow =
        TestWorkflowExtension.newBuilder()
            .registerWorkflowImplementationTypes(RefreshIngestDbViewsWorkflowImpl.class)
            .setDoNotStart(true)
            .build();

    /**
     * Test that a single signal triggers exactly one refresh.
     */
    @Test
    void testSingleSignalTriggersOneRefresh(TestWorkflowEnvironment testEnv, Worker worker,
            WorkflowClient workflowClient) {
        // Track refresh calls
        AtomicInteger refreshCount = new AtomicInteger(0);

        // Create activity implementations
        RefreshIngestDbViewsActivity refreshActivity = input -> {
            refreshCount.incrementAndGet();
            return new RefreshIngestDbViewsOutput();
        };

        CheckRunningTransformersActivity checkActivity = () -> false;

        // Register activities
        worker.registerActivitiesImplementations(refreshActivity, checkActivity);
        testEnv.start();

        // Create workflow stub
        RefreshIngestDbViewsWorkflow workflow = workflowClient.newWorkflowStub(
            RefreshIngestDbViewsWorkflow.class,
            WorkflowOptions.newBuilder()
                .setWorkflowId("test-refresh-workflow")
                .setTaskQueue(worker.getTaskQueue())
                .build()
        );

        // Start workflow
        WorkflowClient.start(workflow::run);

        // Send signal
        workflow.requestRefresh("source-workflow-1");

        // Let the workflow process
        testEnv.sleep(Duration.ofSeconds(1));

        // Query status
        ViewRefreshStatus status = workflow.getStatus();
        assertEquals(1, status.totalRefreshes());
        assertFalse(status.isRefreshing());

        // Verify activity was called once
        assertEquals(1, refreshCount.get());
    }

    /**
     * Test that sequential signals each trigger their own refresh.
     */
    @Test
    void testSequentialSignalsEachTriggerRefresh(TestWorkflowEnvironment testEnv, Worker worker,
            WorkflowClient workflowClient) {
        // Track refresh calls
        AtomicInteger refreshCount = new AtomicInteger(0);

        // Create activity implementations
        RefreshIngestDbViewsActivity refreshActivity = input -> {
            refreshCount.incrementAndGet();
            return new RefreshIngestDbViewsOutput();
        };

        // Return true to keep workflow alive between signals
        CheckRunningTransformersActivity checkActivity = () -> true;

        // Register activities
        worker.registerActivitiesImplementations(refreshActivity, checkActivity);
        testEnv.start();

        // Create workflow stub
        RefreshIngestDbViewsWorkflow workflow = workflowClient.newWorkflowStub(
            RefreshIngestDbViewsWorkflow.class,
            WorkflowOptions.newBuilder()
                .setWorkflowId("test-refresh-workflow-sequential")
                .setTaskQueue(worker.getTaskQueue())
                .build()
        );

        // Start workflow
        WorkflowClient.start(workflow::run);

        // Send first signal and wait for it to complete
        workflow.requestRefresh("source-workflow-1");
        testEnv.sleep(Duration.ofSeconds(1));
        assertEquals(1, workflow.getStatus().totalRefreshes());

        // Send second signal and wait for it to complete
        workflow.requestRefresh("source-workflow-2");
        testEnv.sleep(Duration.ofSeconds(1));
        assertEquals(2, workflow.getStatus().totalRefreshes());

        // Send third signal and wait for it to complete
        workflow.requestRefresh("source-workflow-3");
        testEnv.sleep(Duration.ofSeconds(1));

        // Query status - should have exactly 3 refreshes
        ViewRefreshStatus status = workflow.getStatus();
        assertEquals(3, status.totalRefreshes());
        assertFalse(status.isRefreshing());
        assertFalse(status.hasPendingRefresh());

        // Verify activity was called three times
        assertEquals(3, refreshCount.get());
    }

    /**
     * Test that workflow stays alive when transformers are running.
     */
    @Test
    void testWorkflowStaysAliveWhenTransformersRunning(TestWorkflowEnvironment testEnv, Worker worker,
            WorkflowClient workflowClient) {
        // Track calls
        AtomicInteger refreshCount = new AtomicInteger(0);
        AtomicInteger checkCount = new AtomicInteger(0);

        // Create activity implementations
        RefreshIngestDbViewsActivity refreshActivity = input -> {
            refreshCount.incrementAndGet();
            return new RefreshIngestDbViewsOutput();
        };

        // First call returns true (transformers running), subsequent calls return false
        CheckRunningTransformersActivity checkActivity = () -> {
            int count = checkCount.incrementAndGet();
            return count == 1;
        };

        // Register activities
        worker.registerActivitiesImplementations(refreshActivity, checkActivity);
        testEnv.start();

        // Create workflow stub
        RefreshIngestDbViewsWorkflow workflow = workflowClient.newWorkflowStub(
            RefreshIngestDbViewsWorkflow.class,
            WorkflowOptions.newBuilder()
                .setWorkflowId("test-refresh-workflow-transformers")
                .setTaskQueue(worker.getTaskQueue())
                .build()
        );

        // Start workflow
        WorkflowClient.start(workflow::run);

        // Send signal and let it complete
        workflow.requestRefresh("source-workflow-1");
        testEnv.sleep(Duration.ofSeconds(1));

        // Workflow should still be alive after first refresh because transformers are "running"
        ViewRefreshStatus status = workflow.getStatus();
        assertEquals(1, status.totalRefreshes());

        // Verify check was called at least once
        assertEquals(1, checkCount.get());
    }

    /**
     * Test that query returns correct status.
     */
    @Test
    void testQueryReturnsCorrectStatus(TestWorkflowEnvironment testEnv, Worker worker,
            WorkflowClient workflowClient) {
        // Track refresh calls
        AtomicInteger refreshCount = new AtomicInteger(0);

        // Create activity implementations
        RefreshIngestDbViewsActivity refreshActivity = input -> {
            refreshCount.incrementAndGet();
            return new RefreshIngestDbViewsOutput();
        };

        CheckRunningTransformersActivity checkActivity = () -> false;

        // Register activities
        worker.registerActivitiesImplementations(refreshActivity, checkActivity);
        testEnv.start();

        // Create workflow stub
        RefreshIngestDbViewsWorkflow workflow = workflowClient.newWorkflowStub(
            RefreshIngestDbViewsWorkflow.class,
            WorkflowOptions.newBuilder()
                .setWorkflowId("test-refresh-workflow-query")
                .setTaskQueue(worker.getTaskQueue())
                .build()
        );

        // Start workflow
        WorkflowClient.start(workflow::run);

        // Initial status should show not refreshing
        ViewRefreshStatus initialStatus = workflow.getStatus();
        assertFalse(initialStatus.isRefreshing());
        assertEquals(0, initialStatus.totalRefreshes());

        // Send signal
        workflow.requestRefresh("source-workflow-1");

        // Let workflow process
        testEnv.sleep(Duration.ofSeconds(1));

        // Final status should show completed
        ViewRefreshStatus finalStatus = workflow.getStatus();
        assertFalse(finalStatus.isRefreshing());
        assertEquals(1, finalStatus.totalRefreshes());
    }

    /**
     * Test that signals sent before any are processed all result in just one refresh.
     */
    @Test
    void testMultipleSignalsBeforeProcessingResultInOneRefresh(TestWorkflowEnvironment testEnv,
            Worker worker, WorkflowClient workflowClient) {
        // Track refresh calls
        AtomicInteger refreshCount = new AtomicInteger(0);

        // Create activity implementations
        RefreshIngestDbViewsActivity refreshActivity = input -> {
            refreshCount.incrementAndGet();
            return new RefreshIngestDbViewsOutput();
        };

        CheckRunningTransformersActivity checkActivity = () -> false;

        // Register activities
        worker.registerActivitiesImplementations(refreshActivity, checkActivity);
        testEnv.start();

        // Create workflow stub
        RefreshIngestDbViewsWorkflow workflow = workflowClient.newWorkflowStub(
            RefreshIngestDbViewsWorkflow.class,
            WorkflowOptions.newBuilder()
                .setWorkflowId("test-refresh-workflow-batch")
                .setTaskQueue(worker.getTaskQueue())
                .build()
        );

        // Start workflow
        WorkflowClient.start(workflow::run);

        // Send multiple signals rapidly before any are processed
        workflow.requestRefresh("source-workflow-1");
        workflow.requestRefresh("source-workflow-2");
        workflow.requestRefresh("source-workflow-3");

        // Let workflow process
        testEnv.sleep(Duration.ofSeconds(1));

        // Should have exactly 1 refresh since all signals arrived before processing started
        ViewRefreshStatus status = workflow.getStatus();
        assertEquals(1, status.totalRefreshes());

        assertEquals(1, refreshCount.get());
    }
}
