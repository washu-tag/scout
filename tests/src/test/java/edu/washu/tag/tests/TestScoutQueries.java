package edu.washu.tag.tests;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import edu.washu.tag.BaseTest;
import edu.washu.tag.TestQuery;
import edu.washu.tag.TestQuerySuite;
import edu.washu.tag.model.IngestJobInput;
import edu.washu.tag.util.FileIOUtils;
import org.apache.spark.sql.SparkSession;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.testng.annotations.BeforeClass;
import org.testng.annotations.DataProvider;
import org.testng.annotations.Test;

public class TestScoutQueries extends BaseTest {

    private SparkSession spark;
    private static final TestQuerySuite<?> exportedQueries = readQueries();
    private static final Logger logger = LoggerFactory.getLogger(TestScoutQueries.class);
    private static final String TABLE = "testdata" + System.currentTimeMillis();

    @BeforeClass
    private void initSparkSession() {
        spark = SparkSession.builder()
            .appName("TestClient")
            .master("local")
            .config(config.getSparkConfig())
            .enableHiveSupport()
            .getOrCreate();
    }

    @BeforeClass
    private void ingest() {
        temporalClient.launchIngest(
            new IngestJobInput().setReportTableName(TABLE).setLogsRootPath("/data/extraction"),
            true
        );
    }

    @DataProvider(name = "known_queries")
    private Object[][] knownQueries() {
        return exportedQueries
            .getTestQueries()
            .stream()
            .map(query -> new Object[]{query.getId()})
            .toArray(Object[][]::new);
    }

    @Test(dataProvider = "known_queries")
    public void testQueryById(String queryId) {
        runTest(queryId);
    }

    @Test
    public void testRepeatIngest() {
        ingest();
        runTest("all"); // make sure no rows in the whole dataset have been duplicated
        runTest("extended_metadata"); // ...and let's make sure the metadata still looks good
    }

    private static TestQuerySuite<?> readQueries() {
        try {
            return new ObjectMapper().readValue(
                FileIOUtils.readResource("spark_queries.json"),
                TestQuerySuite.class
            );
        } catch (JsonProcessingException e) {
            throw new RuntimeException(e);
        }
    }

    private static TestQuery<?> getQueryById(String id) {
        return exportedQueries
            .getTestQueries()
            .stream()
            .filter(testQuery -> testQuery.getId().equals(id))
            .findFirst()
            .orElseThrow(RuntimeException::new);
    }

    private void runTest(String id) {
        final TestQuery<?> query = getQueryById(id);
        query.setSql(query.getSql().replace(TestQuerySuite.TABLE_PLACEHOLDER, TABLE));
        logger.info("Performing query with spark: {}", query.getSql());
        query.getExpectedQueryResult().validateResult(spark.sql(query.getSql()));
    }

}
