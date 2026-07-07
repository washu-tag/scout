package edu.washu.tag.hl7listener;

import java.io.ByteArrayOutputStream;
import java.util.List;
import java.util.zip.ZipEntry;
import java.util.zip.ZipOutputStream;
import org.apache.camel.Exchange;
import org.apache.camel.Message;
import org.apache.camel.Processor;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

/**
 * Builds a single ZIP archive from a batch of Kafka records. The Camel Kafka batching consumer
 * delivers the whole batch as one exchange whose body is a {@code List} of per-record child
 * exchanges, so the archive is built in a single O(n) pass here — unlike
 * {@code ZipAggregationStrategy}, which rewrites the entire archive for every message (O(n^2)
 * disk IO). Each message becomes one entry named by its HL7 message control id plus the unique
 * Camel exchange id (the exchange id guarantees uniqueness even if two messages share a control
 * id within the same millisecond).
 */
@Component("hl7BatchZipper")
public class Hl7BatchZipper implements Processor {

    private static final Logger LOG = LoggerFactory.getLogger(Hl7BatchZipper.class);

    @Override
    @SuppressWarnings("unchecked")
    public void process(Exchange exchange) throws Exception {
        List<Exchange> batch = exchange.getIn().getBody(List.class);
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
            }
        }
        exchange.getIn().setBody(buffer.toByteArray());
        LOG.info("Zipped {} HL7 messages ({} bytes)", batch.size(), buffer.size());
    }
}
