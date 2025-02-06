package edu.washu.tag.temporal.config;

import io.micrometer.core.instrument.Clock;
import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.registry.otlp.OtlpConfig;
import io.micrometer.registry.otlp.OtlpMeterRegistry;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
public class MetricsConfig {

    @Bean
    public MeterRegistry meterRegistry() {
        // Use the OTLP format for collecting and sending metrics
        OtlpConfig otlpConfig = new OtlpConfig() {
            @Override
            public String get(final String key) {
                return null;
            }
        };

        MeterRegistry registry = new OtlpMeterRegistry(otlpConfig, Clock.SYSTEM);
        return registry;
    }
}