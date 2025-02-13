package edu.washu.tag.temporal.config;

import io.opentelemetry.api.GlobalOpenTelemetry;
import io.opentelemetry.api.OpenTelemetry;

import io.opentelemetry.opentracingshim.OpenTracingShim;
import io.opentracing.Tracer;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
public class OTELConfig {

    @Bean
    public OpenTelemetry openTelemetry() {
        return GlobalOpenTelemetry.get();
    }

    @Bean
    public Tracer openTracingTracer(OpenTelemetry openTelemetry) {
        return OpenTracingShim.createTracerShim(openTelemetry);
    }

}
