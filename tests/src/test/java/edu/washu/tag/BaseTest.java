package edu.washu.tag;

import edu.washu.tag.model.IngestJobDetails;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.testng.annotations.BeforeSuite;

public class BaseTest {

    private static final Logger log = LoggerFactory.getLogger(BaseTest.class);
    protected static IngestJobDetails ingestWorkflow;
    protected final TestConfig config;
    protected final TemporalClient temporalClient;
    public static final String RELAUNCH_PRECURSOR = "relaunch_precursor";

    public BaseTest() {
        config = TestConfig.instance;
        temporalClient = new TemporalClient(config);
    }

    @BeforeSuite
    public void launchIngestion() {
        ingestWorkflow = temporalClient.launchIngest(config.getTemporalConfig().getIngestJobInput(), true);
    }

}
