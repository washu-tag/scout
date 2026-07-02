package edu.washu.tag.hl7listener;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

/**
 * Camel-on-Spring-Boot application that runs the HL7 real-time ingest routes. The routes
 * themselves are declared as YAML DSL resources under {@code src/main/resources/camel};
 * each deployment selects which route(s) to run via {@code camel.main.routes-include-pattern}.
 */
@SpringBootApplication
public class Hl7ListenerApplication {

    public static void main(String[] args) {
        SpringApplication.run(Hl7ListenerApplication.class, args);
    }
}
