package edu.washu.tag.hl7listener;

import java.util.List;
import org.apache.camel.Exchange;
import org.apache.camel.Processor;
import org.apache.camel.component.kafka.consumer.KafkaManualCommit;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

/**
 * Commits the batch's Kafka offsets only AFTER the batch has been durably written downstream
 * (uploaded to object storage and its manifest published). It commits the highest offset per
 * partition (collected by {@link Hl7BatchZipper}) so every partition consumed in the batch
 * advances — committing only the last record's handle would leave other partitions uncommitted,
 * causing persistent consumer lag and re-delivery of those records on the next restart/rebalance.
 *
 * <p>Deferring the commit to the end of the route (with {@code autoCommitEnable=false} +
 * {@code allowManualCommit=true} on the consumer) gives at-least-once delivery: a crash mid-batch
 * replays the batch instead of silently dropping it, since offsets are advanced only after the
 * durable write.
 */
@Component("kafkaBatchCommitter")
public class KafkaBatchCommitter implements Processor {

    private static final Logger LOG = LoggerFactory.getLogger(KafkaBatchCommitter.class);

    @Override
    @SuppressWarnings("unchecked")
    public void process(Exchange exchange) throws Exception {
        List<KafkaManualCommit> commits =
                exchange.getProperty(Hl7BatchZipper.BATCH_COMMITS, List.class);
        if (commits == null || commits.isEmpty()) {
            LOG.warn("No manual-commit handles on the batch; offsets were not committed");
            return;
        }
        for (KafkaManualCommit commit : commits) {
            commit.commit();
        }
        LOG.debug("Committed offsets for {} partition(s)", commits.size());
    }
}
