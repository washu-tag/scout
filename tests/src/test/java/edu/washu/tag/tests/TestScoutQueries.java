package edu.washu.tag.tests;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import edu.washu.tag.BaseTest;
import edu.washu.tag.TestQuery;
import edu.washu.tag.TestQuerySuite;
import edu.washu.tag.util.FileIOUtils;
import io.delta.sql.DeltaSparkSessionExtension;
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

    @BeforeClass
    public void initSparkSession() {
        spark = SparkSession.builder()
            .appName("TestClient")
            .master("local")
            .withExtensions(new DeltaSparkSessionExtension())
            .config(config.getSparkConfig())
            .getOrCreate();
        spark
            .read()
            .format("delta")
            .load(config.getDeltaLakeUrl())
            .createOrReplaceTempView(exportedQueries.getViewName());
    }

    @DataProvider(name = "known_queries")
    public Object[][] knownQueries() {
        return exportedQueries
            .getTestQueries()
            .stream()
            .map(query -> new Object[]{query.getId()})
            .toArray(Object[][]::new);
    }

    @Test(dataProvider = "known_queries")
    public void testQueryById(String queryId) {
        final TestQuery<?> query = exportedQueries
            .getTestQueries()
            .stream()
            .filter(testQuery -> testQuery.getId().equals(queryId))
            .findFirst()
            .orElseThrow(RuntimeException::new);

        logger.info("Performing query with spark: {}", query.getSql());
        query.getExpectedQueryResult().validateResult(spark.sql(query.getSql()));
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

}
