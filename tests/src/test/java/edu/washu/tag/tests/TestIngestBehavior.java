package edu.washu.tag.tests;

import edu.washu.tag.BaseTest;
import edu.washu.tag.model.IngestJobInput;
import edu.washu.tag.model.IngestJobDetails;
import io.temporal.api.common.v1.WorkflowExecution;
import io.temporal.api.enums.v1.WorkflowExecutionStatus;
import io.temporal.client.WorkflowStub;
import org.testng.annotations.Test;

import static org.assertj.core.api.AssertionsForClassTypes.assertThat;

public class TestIngestBehavior extends BaseTest {

    @Test
    public void testIngestImproperLogPath() {
        final String improperLog = "/data/a_file_for_20150101.log";
        final IngestJobDetails improperIngest = temporalClient.launchIngest(
            new IngestJobInput().setLogPaths(improperLog),
            false
        );

    }


}
