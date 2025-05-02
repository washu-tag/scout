package edu.washu.tag;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import edu.washu.tag.util.FileIOUtils;
import java.nio.file.Paths;
import java.util.Map;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

public class TestConfig {

    private Map<String, Object> sparkConfig;
    private DatabaseConfig postgresConfig;
    private TemporalConfig temporalConfig = new TemporalConfig();
    private static final Logger logger = LoggerFactory.getLogger(TestConfig.class);
    public static final TestConfig instance = cache();

    public Map<String, Object> getSparkConfig() {
        return sparkConfig;
    }

    public TestConfig setSparkConfig(Map<String, Object> sparkConfig) {
        this.sparkConfig = sparkConfig;
        return this;
    }

    public DatabaseConfig getPostgresConfig() {
        return postgresConfig;
    }

    public TestConfig setPostgresConfig(DatabaseConfig postgresConfig) {
        this.postgresConfig = postgresConfig;
        return this;
    }

    public TemporalConfig getTemporalConfig() {
        return temporalConfig;
    }

    public TestConfig setTemporalConfig(TemporalConfig temporalConfig) {
        this.temporalConfig = temporalConfig;
        return this;
    }

    private static TestConfig cache() {
        final String providedConfigName = System.getProperty("config");
        final String defaultConfigName = "local.json";
        final boolean defaultOverwritten = providedConfigName != null;
        final String effectivePath = Paths.get(
            "config",
            defaultOverwritten ? providedConfigName : defaultConfigName
        ).toString();
        if (defaultOverwritten) {
            logger.info(
                "Config file specified as {}, attempting to read it from within the test resource directory as: {}",
                providedConfigName,
                effectivePath
            );
        } else {
            logger.info("Config file not specified, attempting to read it from: {}", effectivePath);
        }

        try {
            return new ObjectMapper().readValue(
                FileIOUtils.readResource(effectivePath),
                TestConfig.class
            );
        } catch (JsonProcessingException e) {
            throw new RuntimeException(e);
        }
    }

}