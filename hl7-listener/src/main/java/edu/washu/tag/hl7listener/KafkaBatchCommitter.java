package edu.washu.tag.hl7listener;

import org.apache.camel.Exchange;
import org.apache.camel.Processor;
import org.apache.camel.component.kafka.KafkaConstants;
import org.apache.camel.component.kafka.consumer.KafkaManualCommit;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

/**
 * Commits the Kafka offsets for a consumed batch only AFTER the batch has been durably written
 * downstream (uploaded to object storage and its manifest published). The batching consumer runs
 * with {@code autoCommitEnable=false} + {@code allowManualCommit=true} and places the batch's
 * {@link KafkaManualCommit} on the aggregated exchange; deferring the commit to the end of the
 * route gives at-least-once delivery — a crash mid-batch replays the batch instead of silently
 * dropping it (which auto-commit would do by advancing offsets as records enter the route).
 */
@Component("kafkaBatchCommitter")
public class KafkaBatchCommitter implements Processor {

    private static final Logger LOG = LoggerFactory.getLogger(KafkaBatchCommitter.class);

    @Override
    public void process(Exchange exchange) throws Exception {
        KafkaManualCommit commit =
                exchange.getIn().getHeader(KafkaConstants.MANUAL_COMMIT, KafkaManualCommit.class);
        if (commit != null) {
            commit.commit();
        } else {
            LOG.warn("No KafkaManualCommit on the batch exchange; offsets were not committed");
        }
    }
}
