package edu.washu.tag.extractor.hl7log.util;

import static org.junit.jupiter.api.Assertions.assertEquals;

import org.junit.jupiter.api.Test;

class IngestHl7LogWorkflowInputParserPathTest {

    @Test
    void resolveLogPath_absoluteAndS3Uris_areUsedAsIs() {
        assertEquals("/abs/a.log", IngestHl7LogWorkflowInputParser.resolveLogPath("/data", "/abs/a.log"));
        assertEquals("s3://b/hl7/a.log",
                IngestHl7LogWorkflowInputParser.resolveLogPath("/data", "s3://b/hl7/a.log"));
    }

    @Test
    void resolveLogPath_relativeUnderLocalRoot_isResolved() {
        assertEquals("/data/hl7/a.log", IngestHl7LogWorkflowInputParser.resolveLogPath("/data", "hl7/a.log"));
    }

    @Test
    void resolveLogPath_relativeUnderS3Root_keepsScheme() {
        assertEquals("s3://b/hl7/a.log", IngestHl7LogWorkflowInputParser.resolveLogPath("s3://b/hl7", "a.log"));
        assertEquals("s3://b/hl7/a.log", IngestHl7LogWorkflowInputParser.resolveLogPath("s3://b/hl7/", "a.log"));
    }
}
