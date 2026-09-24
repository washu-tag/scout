package edu.washu.tag.extractor.hl7log.util;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import org.junit.jupiter.api.Test;

class IngestHl7LogWorkflowInputParserPathTest {

    @Test
    void isAbsolute_localAbsoluteAndS3Uris() {
        assertTrue(IngestHl7LogWorkflowInputParser.isAbsolute("/abs/a.log"));
        assertTrue(IngestHl7LogWorkflowInputParser.isAbsolute("s3://b/a.log"));
        assertFalse(IngestHl7LogWorkflowInputParser.isAbsolute("relative/a.log"));
    }

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
