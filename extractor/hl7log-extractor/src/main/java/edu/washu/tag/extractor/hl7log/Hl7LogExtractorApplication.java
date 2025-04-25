package edu.washu.tag.extractor.hl7log;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.retry.annotation.EnableRetry;

@SpringBootApplication
@EnableRetry
public class Hl7LogExtractorApplication {

    public static void main(String[] args) {
        SpringApplication.run(Hl7LogExtractorApplication.class, args);
    }

}
