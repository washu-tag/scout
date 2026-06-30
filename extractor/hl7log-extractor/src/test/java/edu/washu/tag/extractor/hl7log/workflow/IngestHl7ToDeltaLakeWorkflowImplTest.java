package edu.washu.tag.extractor.hl7log.workflow;

import static edu.washu.tag.extractor.hl7log.util.Constants.BUILD_MANIFEST_QUEUE;
import static edu.washu.tag.extractor.hl7log.util.Constants.INGEST_DELTA_LAKE_QUEUE;
import static edu.washu.tag.extractor.hl7log.util.Constants.REFRESH_VIEWS_QUEUE;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import edu.washu.tag.extractor.hl7log.activity.FindHl7Files;
import edu.washu.tag.extractor.hl7log.activity.SignalRefreshActivity;
import edu.washu.tag.extractor.hl7log.model.FindHl7FilesInput;
import edu.washu.tag.extractor.hl7log.model.FindHl7FilesOutput;
import edu.washu.tag.extractor.hl7log.model.IngestHl7FilesToDeltaLakeInput;
import edu.washu.tag.extractor.hl7log.model.IngestHl7FilesToDeltaLakeOutput;
import edu.washu.tag.extractor.hl7log.model.WriteHl7FilesErrorStatusToDbInput;
import edu.washu.tag.extractor.hl7log.model.WriteHl7FilesErrorStatusToDbOutput;
import edu.washu.tag.extractor.hl7log.util.DefaultArgs;
import io.temporal.activity.Activity;
import io.temporal.activity.DynamicActivity;
import io.temporal.client.WorkflowException;
import io.temporal.client.WorkflowOptions;
import io.temporal.testing.TestWorkflowEnvironment;
import io.temporal.worker.Worker;
import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.Timeout;

/**
 * Unit tests for IngestHl7ToDeltaLakeWorkflow: verifies the base/derivative split (#457)
 * sequencing and failure routing without a real cluster.
 *
 * <p>The base ingest and derivative steps are invoked via untyped stubs (their impl is the
 * Python worker), so they are mocked with a single {@link DynamicActivity} that dispatches
 * on activity type. {@link FindHl7Files} and {@link SignalRefreshActivity} are normal typed
 * mocks. The workflow dispatches activities to three task queues, so a worker is started for
 * each.
 */
@Timeout(value = 60, unit = TimeUnit.SECONDS) // fail fast — never hang the build on a wiring bug
class IngestHl7ToDeltaLakeWorkflowImplTest {

    private static final String INGEST_ACTIVITY = "ingest_hl7_files_to_delta_lake";
    private static final String DERIVE_ACTIVITY = "derive_delta_tables";

    private TestWorkflowEnvironment env;
    private final List<String> calls = new CopyOnWriteArrayList<>();
    private final AtomicBoolean writeErrorCalled = new AtomicBoolean(false);
    // Set in a test to make the matching Python (untyped) activity fail; null = all succeed.
    private volatile String failActivityType = null;

    @BeforeEach
    void setUp() {
        // DefaultArgs is a Spring @Component populated via @Value setters; there is no
        // Spring context here, so seed its static defaults directly. Short timeouts keep
        // the test snappy. Without this the workflow does Duration.ofMinutes(null) -> NPE.
        DefaultArgs defaults = new DefaultArgs();
        defaults.setDeltaIngestTimeout(1);
        defaults.setDeriveDeltaTablesTimeout(1);
        defaults.setReportTableName("reports");

        env = TestWorkflowEnvironment.newInstance();

        // Python activities (untyped, INGEST_DELTA_LAKE_QUEUE) via a DynamicActivity.
        Worker ingestWorker = env.newWorker(INGEST_DELTA_LAKE_QUEUE);
        ingestWorker.registerWorkflowImplementationTypes(IngestHl7ToDeltaLakeWorkflowImpl.class);
        ingestWorker.registerActivitiesImplementations(
            (DynamicActivity) args -> {
                String type = Activity.getExecutionContext().getInfo().getActivityType();
                calls.add(type);
                if (type.equals(failActivityType)) {
                    throw new RuntimeException("injected failure: " + type);
                }
                if (INGEST_ACTIVITY.equals(type)) {
                    return new IngestHl7FilesToDeltaLakeOutput(42);
                }
                return null; // derive_delta_tables returns void
            });

        // Typed activities on their own queues.
        Worker manifestWorker = env.newWorker(BUILD_MANIFEST_QUEUE);
        manifestWorker.registerActivitiesImplementations(new FindHl7Files() {
            @Override
            public FindHl7FilesOutput findHl7FilesAndWriteManifest(FindHl7FilesInput input) {
                calls.add("findHl7FilesAndWriteManifest"); // not reached: manifest is provided
                return null;
            }

            @Override
            public WriteHl7FilesErrorStatusToDbOutput writeHl7FilesErrorStatusToDb(
                WriteHl7FilesErrorStatusToDbInput input) {
                writeErrorCalled.set(true);
                calls.add("writeHl7FilesErrorStatusToDb");
                return null;
            }
        });

        Worker refreshWorker = env.newWorker(REFRESH_VIEWS_QUEUE);
        refreshWorker.registerActivitiesImplementations(
            (SignalRefreshActivity) sourceWorkflowId -> calls.add("signalRefresh"));

        env.start();
    }

    @AfterEach
    void tearDown() {
        env.close();
    }

    private IngestHl7FilesToDeltaLakeOutput runWorkflow() {
        IngestHl7ToDeltaLakeWorkflow workflow = env.getWorkflowClient().newWorkflowStub(
            IngestHl7ToDeltaLakeWorkflow.class,
            WorkflowOptions.newBuilder().setTaskQueue(INGEST_DELTA_LAKE_QUEUE).build());
        // Non-empty manifest path so the workflow skips findHl7FilesAndWriteManifest.
        return workflow.ingestHl7FileToDeltaLake(
            new IngestHl7FilesToDeltaLakeInput(null, "manifest.txt", null, "reports", null, true));
    }

    @Test
    void happyPathRunsBaseThenSignalThenDerive() {
        IngestHl7FilesToDeltaLakeOutput output = runWorkflow();

        assertEquals(42, output.numHl7Ingested());
        assertEquals(List.of(INGEST_ACTIVITY, "signalRefresh", DERIVE_ACTIVITY), calls);
        assertFalse(writeErrorCalled.get(), "no files should be marked failed on success");
    }

    @Test
    void baseFailureMarksFilesFailedAndSkipsDerive() {
        failActivityType = INGEST_ACTIVITY;

        assertThrows(WorkflowException.class, this::runWorkflow);

        assertTrue(writeErrorCalled.get(), "base failure should mark files failed");
        assertFalse(calls.contains("signalRefresh"), "signal should not fire after base failure");
        assertFalse(calls.contains(DERIVE_ACTIVITY), "derive should not run after base failure");
    }

    @Test
    void derivativeFailureDoesNotMarkFilesFailed() {
        failActivityType = DERIVE_ACTIVITY;

        assertThrows(WorkflowException.class, this::runWorkflow);

        assertFalse(writeErrorCalled.get(),
            "a derivative failure must NOT mark files failed (they are in the base table)");
        assertTrue(calls.contains("signalRefresh"), "signal fires before the derive step");
    }
}
