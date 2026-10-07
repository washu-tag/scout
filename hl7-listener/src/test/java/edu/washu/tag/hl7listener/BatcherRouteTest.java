package edu.washu.tag.hl7listener;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Properties;
import org.apache.camel.Exchange;
import org.apache.camel.builder.AdviceWith;
import org.apache.camel.component.kafka.KafkaConstants;
import org.apache.camel.component.mock.MockEndpoint;
import org.apache.camel.spi.Registry;
import org.apache.camel.spi.Resource;
import org.apache.camel.support.DefaultExchange;
import org.apache.camel.support.PluginHelper;
import org.apache.camel.test.junit6.CamelContextConfiguration;
import org.apache.camel.test.junit6.CamelTestSupport;
import org.apache.camel.test.junit6.TestExecutionConfiguration;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

/**
 * Loads the real batcher route and stubs its Kafka consumer, S3 upload and manifest publish, to
 * check how it fails: a failed batch must stay failed (so breakOnFirstError redelivers it) and
 * only after the retry pause, so a backlog is not retried back-to-back.
 */
class BatcherRouteTest extends CamelTestSupport {

    private static final long RETRY_DELAY_MS = 300;

    @Override
    public void configureTest(TestExecutionConfiguration config) {
        super.configureTest(config);
        config.withUseAdviceWith(true);
    }

    @Override
    public void configureContext(CamelContextConfiguration config) {
        super.configureContext(config);
        config.withUseOverridePropertiesWithPropertiesComponent(routeProperties());
    }

    private static Properties routeProperties() {
        Properties props = new Properties();
        props.put("kafka.hl7.topic", "hl7-messages");
        props.put("kafka.hl7.batches.topic", "hl7-batches");
        props.put("kafka.brokers", "localhost:9092");
        props.put("hl7.batcher.group.id", "hl7-batcher");
        props.put("hl7.batcher.max.poll.records", "1000");
        props.put("hl7.batcher.poll.timeout.ms", "30000");
        props.put("hl7.batcher.batching.interval.ms", "1200000");
        props.put("hl7.batcher.retry.delay.ms", String.valueOf(RETRY_DELAY_MS));
        props.put("s3.bucket", "hl7-raw");
        props.put("aws.region", "us-east-1");
        props.put("s3.use.default.creds", "false");
        props.put("s3.override.endpoint", "true");
        props.put("s3.endpoint", "http://localhost:9000");
        props.put("s3.force.path.style", "true");
        return props;
    }

    @Override
    protected void bindToRegistry(Registry registry) {
        registry.bind("hl7BatchZipper", new Hl7BatchZipper());
        registry.bind("kafkaBatchCommitter", new KafkaBatchCommitter());
    }

    @BeforeEach
    void loadRouteWithStubbedEndpoints() throws Exception {
        Resource route = PluginHelper.getResourceLoader(context).resolveResource("classpath:camel/batcher.camel.yaml");
        PluginHelper.getRoutesLoader(context).loadRoutes(route);
        AdviceWith.adviceWith(context, "batchHl7Messages", a -> {
            a.replaceFromWith("direct:batch");
            a.weaveByToUri("aws2-s3*").replace().to("mock:s3");
            a.weaveByToUri("kafka*").replace().to("mock:manifest");
        });
        context.start();
    }

    @Test
    void failedUploadPausesThenLeavesTheBatchFailed() throws Exception {
        getMockEndpoint("mock:s3").whenAnyExchangeReceived(e -> {
            throw new IllegalStateException("simulated S3 timeout");
        });
        MockEndpoint manifest = getMockEndpoint("mock:manifest");
        manifest.expectedMessageCount(0);

        long start = System.nanoTime();
        Exchange result = template.send("direct:batch", batchOf("MSH|^~\\&|TEST|||||||ORU^R01|ctrl-1|P|2.5"));
        long elapsedMs = (System.nanoTime() - start) / 1_000_000;

        assertTrue(result.isFailed(), "a failed upload must fail the exchange so the batch is redelivered");
        assertTrue(elapsedMs >= RETRY_DELAY_MS, "expected the retry pause, took " + elapsedMs + " ms");
        manifest.assertIsSatisfied();
    }

    @Test
    void successfulUploadDoesNotPause() throws Exception {
        MockEndpoint manifest = getMockEndpoint("mock:manifest");
        manifest.expectedMessageCount(1);

        long start = System.nanoTime();
        Exchange result = template.send("direct:batch", batchOf("MSH|^~\\&|TEST|||||||ORU^R01|ctrl-2|P|2.5"));
        long elapsedMs = (System.nanoTime() - start) / 1_000_000;

        assertFalse(result.isFailed(), "upload succeeded, so the batch must not fail");
        assertTrue(elapsedMs < RETRY_DELAY_MS, "no pause expected on success, took " + elapsedMs + " ms");
        manifest.assertIsSatisfied();
    }

    /** One batch exchange as the Kafka batching consumer delivers it: a List of record exchanges. */
    private Exchange batchOf(String hl7) {
        Exchange record = new DefaultExchange(context);
        record.getMessage().setBody(hl7.getBytes(StandardCharsets.UTF_8));
        record.getMessage().setHeader(KafkaConstants.KEY, "ctrl");
        Exchange batch = new DefaultExchange(context);
        batch.getMessage().setBody(List.of(record));
        return batch;
    }
}
