package edu.washu.tag.extractor.hl7log;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.retry.annotation.EnableRetry;

/**
 * Main application class for the HL7 Log Extractor.
 */
@SpringBootApplication
@EnableRetry
public class Hl7LogExtractorApplication {

    /**
     * Main method to run the HL7 Log Extractor application.
     *
     * @param args command line arguments
     */
    public static void main(String[] args) {
        SpringApplication.run(Hl7LogExtractorApplication.class, args);
    }

}
