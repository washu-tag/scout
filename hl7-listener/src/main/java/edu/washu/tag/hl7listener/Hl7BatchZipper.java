package edu.washu.tag.hl7listener;

import java.io.ByteArrayOutputStream;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.zip.ZipEntry;
import java.util.zip.ZipOutputStream;
import org.apache.camel.Exchange;
import org.apache.camel.Message;
import org.apache.camel.Processor;
import org.apache.camel.component.kafka.KafkaConstants;
import org.apache.camel.component.kafka.consumer.KafkaManualCommit;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

/**
 * Builds a single ZIP archive from a batch of Kafka records and stashes the batch's per-partition
 * manual-commit handles for {@link KafkaBatchCommitter} to apply after the durable write.
 *
 * <p>The Camel Kafka batching consumer delivers the whole batch as one exchange whose body is a
 * {@code List} of per-record child exchanges, so the archive is built in a single O(n) pass here
 * — unlike {@code ZipAggregationStrategy}, which rewrites the entire archive for every message
 * (O(n^2) disk IO). Each message becomes one entry named by its HL7 message control id plus the
 * unique Camel exchange id (guaranteeing uniqueness even if two messages share a control id
 * within the same millisecond).
 *
 * <p>The batch spans all assigned partitions, and each record's {@link KafkaManualCommit} commits
 * only its own partition. Committing just the last record's handle would leave other partitions'
 * offsets uncommitted (persistent consumer lag and re-delivery on restart), so we record the
 * highest-offset handle per partition and commit them all after the upload.
 */
@Component("hl7BatchZipper")
public class Hl7BatchZipper implements Processor {

    private static final Logger LOG = LoggerFactory.getLogger(Hl7BatchZipper.class);

    /** Exchange property carrying the per-partition manual-commit handles for the batch. */
    static final String BATCH_COMMITS = "hl7BatchCommits";

    @Override
    @SuppressWarnings("unchecked")
    public void process(Exchange exchange) throws Exception {
        List<Exchange> batch = exchange.getIn().getBody(List.class);

        Map<Integer, Long> maxOffset = new HashMap<>();
        Map<Integer, KafkaManualCommit> commits = new HashMap<>();

        ByteArrayOutputStream buffer = new ByteArrayOutputStream();
        try (ZipOutputStream zip = new ZipOutputStream(buffer)) {
            for (Exchange child : batch) {
                Message message = child.getMessage();
                String key = message.getHeader("kafka.KEY", "unknown", String.class);
                byte[] body = message.getBody(byte[].class);
                if (body == null) {
                    body = new byte[0];
                }
                zip.putNextEntry(new ZipEntry(key + "-" + child.getExchangeId() + ".hl7"));
                zip.write(body);
                zip.closeEntry();

                Integer partition = message.getHeader(KafkaConstants.PARTITION, Integer.class);
                Long offset = message.getHeader(KafkaConstants.OFFSET, Long.class);
                KafkaManualCommit commit =
                        message.getHeader(KafkaConstants.MANUAL_COMMIT, KafkaManualCommit.class);
                if (partition != null && offset != null && commit != null
                        && offset >= maxOffset.getOrDefault(partition, Long.MIN_VALUE)) {
                    maxOffset.put(partition, offset);
                    commits.put(partition, commit);
                }
            }
        }
        exchange.getIn().setBody(buffer.toByteArray());
        exchange.setProperty(BATCH_COMMITS, new ArrayList<>(commits.values()));
        LOG.info("Zipped {} HL7 messages ({} bytes) across {} partition(s)",
                batch.size(), buffer.size(), commits.size());
    }
}
