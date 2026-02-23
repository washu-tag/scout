package edu.washu.tag;

public class BaseTest {

    protected final TestConfig config;
    protected final TemporalClient temporalClient;

    public BaseTest() {
        config = TestConfig.instance;
        temporalClient = new TemporalClient(config);
    }

}
