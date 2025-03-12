package edu.washu.tag.temporal;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.retry.annotation.EnableRetry;

@SpringBootApplication
@EnableRetry
public class TemporalJavaApplication {

    public static void main(String[] args) {
        SpringApplication.run(TemporalJavaApplication.class, args);
    }

}
