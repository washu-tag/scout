package edu.washu.tag.extractor.hl7log.activity;

import static edu.washu.tag.extractor.hl7log.util.Constants.REFRESH_VIEWS_HEARTBEAT_INTERVAL_SECONDS;
import static edu.washu.tag.extractor.hl7log.util.Constants.REFRESH_VIEWS_PROCEDURE_NAME;
import static edu.washu.tag.extractor.hl7log.util.Constants.REFRESH_VIEWS_QUEUE;

import edu.washu.tag.extractor.hl7log.model.RefreshIngestDbViewsInput;
import edu.washu.tag.extractor.hl7log.model.RefreshIngestDbViewsOutput;
import io.temporal.activity.Activity;
import io.temporal.activity.ActivityExecutionContext;
import io.temporal.activity.ActivityInfo;
import io.temporal.client.ActivityCanceledException;
import io.temporal.spring.boot.ActivityImpl;
import io.temporal.workflow.Workflow;
import java.sql.Connection;
import java.sql.SQLException;
import java.sql.Statement;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;
import org.slf4j.Logger;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;

@Component
@ActivityImpl(taskQueues = REFRESH_VIEWS_QUEUE)
public class RefreshIngestDbViewsActivityImpl implements RefreshIngestDbViewsActivity {
    private static final Logger logger = Workflow.getLogger(RefreshIngestDbViewsActivityImpl.class);

    private final JdbcTemplate jdbcTemplate;

    public RefreshIngestDbViewsActivityImpl(JdbcTemplate jdbcTemplate) {
        this.jdbcTemplate = jdbcTemplate;
    }

    @Override
    public RefreshIngestDbViewsOutput refreshIngestDbViews(RefreshIngestDbViewsInput input) {
        final ActivityExecutionContext ctx = Activity.getExecutionContext();
        final ActivityInfo activityInfo = ctx.getInfo();
        final String workflowId = activityInfo.getWorkflowId();
        final String activityId = activityInfo.getActivityId();
        logger.info("WorkflowId {} ActivityId {} - Beginning activity to refresh ingest views from database", workflowId, activityId);

        // Initialize state for tracking completion, cancellation, and errors
        AtomicBoolean cancelled = new AtomicBoolean(false);
        AtomicReference<Statement> statementRef = new AtomicReference<>();
        AtomicReference<Throwable> errorRef = new AtomicReference<>();

        // Run the database operation in a separate thread
        CompletableFuture<Void> dbFuture = CompletableFuture.runAsync(() -> {
            logger.info("WorkflowId {} ActivityId {} - Starting database operation to refresh views", workflowId, activityId);
            Statement stmt = null;
            try (Connection conn = jdbcTemplate.getDataSource().getConnection()) {
                stmt = conn.createStatement();
                statementRef.set(stmt);

                // Execute the stored procedure
                stmt.execute("CALL " + REFRESH_VIEWS_PROCEDURE_NAME + "()");

                logger.info("WorkflowId {} ActivityId {} - Successfully executed refresh views procedure", workflowId, activityId);
            } catch (Exception e) {
                // If the activity was cancelled we don't want to log the error because it is likely due to cancellation
                if (!cancelled.get()) {
                    errorRef.set(e);
                    logger.error("WorkflowId {} ActivityId {} - Error refreshing views", workflowId, activityId, e);
                }
            } finally {
                closeQuietly(stmt);
            }
        });

        try {
            // Heartbeat loop while waiting for the database operation to complete
            while (!dbFuture.isDone()) {
                logger.debug("WorkflowId {} ActivityId {} - Heartbeat for refresh views activity", workflowId, activityId);
                ctx.heartbeat(null);

                // Sleep until next heartbeat
                try {
                    Thread.sleep(REFRESH_VIEWS_HEARTBEAT_INTERVAL_SECONDS * 1000);
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                    throw new RuntimeException("Heartbeat interrupted", e);
                }
            }

            logger.info("WorkflowId {} ActivityId {} - Database operation completed", workflowId, activityId);

            // Check if there was an error
            if (errorRef.get() != null) {
                Throwable error = errorRef.get();
                throw RuntimeException.class.isAssignableFrom(error.getClass())
                    ? (RuntimeException) error
                    : new RuntimeException(error);
            }

        } catch (ActivityCanceledException e) {
            logger.info("WorkflowId {} ActivityId {} - Activity cancelled, interrupting database operation", workflowId, activityId);

            // Cancel the database operation
            Statement stmt = statementRef.get();
            if (stmt != null) {
                try {
                    stmt.cancel();
                    logger.info("WorkflowId {} ActivityId {} - Sent explicit cancellation to database", workflowId, activityId);
                } catch (SQLException ex) {
                    logger.warn("WorkflowId {} ActivityId {} - Failed to cancel database operation", workflowId, activityId, ex);
                }
            }

            cancelled.set(true);
            dbFuture.cancel(true);
            throw e;
        }

        logger.info("WorkflowId {} ActivityId {} - Successfully refreshed ingest views", workflowId, activityId);
        return new RefreshIngestDbViewsOutput();
    }

    private void closeQuietly(AutoCloseable closeable) {
        if (closeable != null) {
            try {
                closeable.close();
            } catch (Exception ignored) {
                // Ignore close exceptions
            }
        }
    }
}
