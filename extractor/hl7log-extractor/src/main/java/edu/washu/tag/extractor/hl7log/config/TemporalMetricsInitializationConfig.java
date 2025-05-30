package edu.washu.tag.extractor.hl7log.config;

import static edu.washu.tag.extractor.hl7log.util.Constants.INGEST_DELTA_LAKE_QUEUE;
import static edu.washu.tag.extractor.hl7log.util.Constants.PARENT_QUEUE;
import static edu.washu.tag.extractor.hl7log.util.Constants.REFRESH_VIEWS_QUEUE;

import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.MeterRegistry;
import jakarta.annotation.PostConstruct;
import java.util.Map;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Configuration;

@Configuration
@EnableConfigurationProperties
public class TemporalMetricsInitializationConfig {
    private final MeterRegistry meterRegistry;

    public TemporalMetricsInitializationConfig(MeterRegistry meterRegistry) {
        this.meterRegistry = meterRegistry;
    }

    @PostConstruct
    public void initializeWorkflowMetrics() {
        Map<String, String> workflowToTaskQueue = Map.of(
            "IngestHl7LogWorkflow", PARENT_QUEUE,
            "IngestHl7ToDeltaLakeWorkflow", INGEST_DELTA_LAKE_QUEUE,
            "RefreshIngestDbViewsWorkflow", REFRESH_VIEWS_QUEUE
        );

        // Initialize counters with zero value
        workflowToTaskQueue.forEach((workflowType, taskQueue) -> {
            Counter.builder("temporal_workflow_failed_total")
                .tag("activity_type", "none")
                .tag("exception", "none")
                .tag("namespace", "default")
                .tag("instance", "hl7log-extractor.extractor:8080")
                .tag("job", "hl7log-extractor")
                .tag("operation", "none")
                .tag("query_type", "none")
                .tag("signal_name", "none")
                .tag("status_code", "none")
                .tag("task_queue", taskQueue)
                .tag("worker_type", "none")
                .tag("workflow_type", workflowType)
                .register(meterRegistry)
                .increment(0);
        });
    }
}
