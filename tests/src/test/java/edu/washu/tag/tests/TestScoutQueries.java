package edu.washu.tag.tests;

import static org.assertj.core.api.Assertions.assertThat;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import edu.washu.tag.BaseTest;
import edu.washu.tag.TestQuery;
import edu.washu.tag.TestQuerySuite;
import edu.washu.tag.model.IngestJobInput;
import edu.washu.tag.util.FileIOUtils;
import edu.washu.tag.validation.ExactRowsResult;
import java.util.Arrays;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.function.Consumer;
import java.util.function.Function;
import java.util.function.Supplier;
import java.util.stream.Collectors;
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
    public static final String TABLE = newTable();
    public static final String SELECT_ALL_SQL = "SELECT * FROM " + TestQuerySuite.TABLE_PLACEHOLDER;
    public static final String COLUMN_FILLER_ORDER = "obr_3_filler_order_number";
    public static final String COLUMN_ACCESSION_NUMBER = "accession_number";
    public static final String COLUMN_SOURCE_FILE = "source_file";
    public static final String COLUMN_MESSAGE_CONTROL_ID = "message_control_id";
    public static final String COLUMN_PRIMARY_REPORT_IDENTIFIER = "primary_report_identifier";

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
        ingest(new IngestJobInput().setReportTableName(TABLE).setLogsRootPath("/data/extraction"));
    }

    private void ingest(IngestJobInput ingestJobInput) {
        temporalClient.launchIngest(ingestJobInput, true);
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

    @Test
    public void testProcessedPatientIdIngestion() {
        final String baseTableName = newTable();
        ingest(
            new IngestJobInput().setReportTableName(baseTableName).setLogsRootPath("/data/patient_ids")
        );

        final Map<String, Map<String, String>> rowAssertions = new HashMap<>();
        final Function<String, Map<String, String>> baseAssertionMap = (messageId) -> {
            final Map<String, String> map = new HashMap<>();
            rowAssertions.put(messageId, map);
            for (String id : Arrays.asList("mpi", "slch_mr", "bjwc_mr", "bjh_mr", "epic_mrn", "mbmc_mr")) {
                map.put(id, null);
            }
            return map;
        };
        final String patientIdColumn = "primary_patient_identifier";

        final Map<String, String> legacyReport = baseAssertionMap.apply("1.2.3.4.5");
        legacyReport.put("mpi", "999999-001");
        legacyReport.put(patientIdColumn, "mpi_999999-001");

        final Map<String, String> slchReport = baseAssertionMap.apply("1.2.3.4.6");
        slchReport.put("slch_mr", "999999-S001");
        slchReport.put(patientIdColumn, "slch_mr_999999-S001");

        final Map<String, String> bjwcReport = baseAssertionMap.apply("1.2.3.4.7");
        bjwcReport.put("bjwc_mr", "999999-B001");
        bjwcReport.put(patientIdColumn, "bjwc_mr_999999-B001");

        final Map<String, String> bjhReport = baseAssertionMap.apply("1.2.3.4.8");
        bjhReport.put("bjh_mr", "999999-H001");
        bjhReport.put(patientIdColumn, "bjh_mr_999999-H001");

        final Map<String, String> epicReport = baseAssertionMap.apply("1.2.3.4.9");
        epicReport.put("epic_mrn", "999999-EPIC0001");
        epicReport.put("empi_mr", "999999-001");
        epicReport.put(patientIdColumn, "epic_mrn_999999-EPIC0001");

        final Map<String, String> mbmcReport = baseAssertionMap.apply("1.2.3.4.10");
        mbmcReport.put("mbmc_mr", "999999-M00001");
        mbmcReport.put(patientIdColumn, "mbmc_mr_999999-M00001");

        final ExactRowsResult expected = new ExactRowsResult();
        expected.setRowAssertions(rowAssertions);
        expected.setUniqueIdColumnName(COLUMN_MESSAGE_CONTROL_ID);
        final TestQuery<?> testQuery = new TestQuery<>("processed_patient_ids", SELECT_ALL_SQL);
        testQuery.setExpectedQueryResult(expected);
        queryAndValidate(testQuery, curatedTable(baseTableName));
    }

    @Test
    public void testCurationMerges() {
        final String tableName = newTable();
        final String curatedTable = curatedTable(tableName);
        final String latestTable = latestTable(tableName);
        final String diagnosisTable = diagnosisTable(tableName);
        ingest(new IngestJobInput().setReportTableName(tableName).setLogsRootPath("/data/extraction"));

        final Supplier<TestQuery<?>> readQuery = () -> readFileAs("merge_base.json", new TypeReference<TestQuery<?>>() {});
        final Function<TestQuery<?>, TestQuery<?>> curateTransformer = (testQuery) -> {
            testQuery.setSql(testQuery.getSql().replace(COLUMN_FILLER_ORDER, COLUMN_ACCESSION_NUMBER));
            final ExactRowsResult expectedResult = (ExactRowsResult) testQuery.getExpectedQueryResult();
            for (Map<String, String> assertions : expectedResult.getRowAssertions().values()) {
                assertions.put(COLUMN_PRIMARY_REPORT_IDENTIFIER, assertions.remove(COLUMN_SOURCE_FILE));
                assertions.put(COLUMN_ACCESSION_NUMBER, assertions.remove(COLUMN_FILLER_ORDER));
            }
            return testQuery;
        };

        final TestQuery<?> baseTableQuery = readQuery.get();
        queryAndValidate(baseTableQuery, tableName);

        final TestQuery<?> curatedTableQuery = curateTransformer.apply(readQuery.get());
        queryAndValidate(curatedTableQuery, curatedTable);

        final TestQuery<?> latestTableQuery = curateTransformer.apply(readQuery.get());
        queryAndValidate(latestTableQuery, latestTable);

        // Diagnoses are stored by UID in json. Read them in and create n rows
        final Map<String, List<Map<String, String>>> diagnoses = readFileAs("diagnoses.json", new TypeReference<>() {});
        final Consumer<TestQuery<?>> diagnosisReader = (testQuery) -> {
            final Map<String, Map<String, String>> assertions = new HashMap<>();
            final ExactRowsResult expectedResult = (ExactRowsResult) testQuery.getExpectedQueryResult();
            expectedResult.setUniqueIdColumnName("diagnosis_id");
            for (Map.Entry<String, Map<String, String>> entry : expectedResult.getRowAssertions().entrySet()) {
                if (diagnoses.containsKey(entry.getKey())) {
                    int diagnosisIndex = 0;
                    for (Map<String, String> diagnosis : diagnoses.get(entry.getKey())) {
                        diagnosis.putAll(entry.getValue());
                        assertions.put(diagnosis.get(COLUMN_PRIMARY_REPORT_IDENTIFIER) + "_" + diagnosisIndex, diagnosis);
                        diagnosisIndex++;
                    }
                }
            }
            expectedResult.setRowAssertions(assertions);
        };

        final TestQuery<?> diagnosisTableQuery = curateTransformer.apply(readQuery.get());
        diagnosisReader.accept(diagnosisTableQuery);
        queryAndValidate(diagnosisTableQuery, diagnosisTable);

        ingest(new IngestJobInput().setReportTableName(tableName).setLogsRootPath("/data/curation"));

        final Map<String, Map<String, String>> newReportAssertions = readFileAs("merge_additions.json", new TypeReference<>() {});
        final ExactRowsResult baseTableExpectation = (ExactRowsResult) baseTableQuery.getExpectedQueryResult();
        baseTableExpectation.getRowAssertions().putAll(newReportAssertions);
        queryAndValidate(baseTableQuery, tableName);

        final TestQuery<?> reusedTableQueryPostMerge = curateTransformer.apply(baseTableQuery);
        queryAndValidate(reusedTableQueryPostMerge, curatedTable);

        final ExactRowsResult latestPostMergeExpectation = (ExactRowsResult) reusedTableQueryPostMerge.getExpectedQueryResult();
        final Map<String, Map<String, String>> prefilteredExpectation = latestPostMergeExpectation.getRowAssertions();
        final Set<String> latestReportIds = Set.of("1.2.4.8.16.1", "1.2.4.8.16.2", "1.2.4.8.16.4", "1.2.4.8.16.6");

        latestPostMergeExpectation.setRowAssertions(
            latestReportIds
                .stream()
                .filter(prefilteredExpectation::containsKey)
                .collect(Collectors.toMap(k -> k, prefilteredExpectation::get))
        );
        queryAndValidate(reusedTableQueryPostMerge, latestTable);

        // now we can replace latestPostMergeExpectation with diagnoses

        diagnosisReader.accept(reusedTableQueryPostMerge);
        queryAndValidate(reusedTableQueryPostMerge, diagnosisTable);
    }

    @Test
    public void testCuratedColumns() {
        assertThat(
            spark.sql("SHOW COLUMNS FROM " + curatedTable(TABLE))
                .collectAsList()
                .stream()
                .map(row -> row.getString(0))
                .toList()
        ).as("Columns in curated table").contains(
            COLUMN_ACCESSION_NUMBER
        ).doesNotContainAnyElementsOf(Arrays.asList(
            COLUMN_SOURCE_FILE,
            "orc_2_placer_order_number",
            "obr_2_placer_order_number",
            "orc_3_filler_order_number",
            COLUMN_FILLER_ORDER,
            "filler_order_number"
        ));
    }

    private static <T> T readFileAs(String resourceName, TypeReference<T> classObj) {
        try {
            return new ObjectMapper().readValue(
                FileIOUtils.readResource(resourceName),
                classObj
            );
        } catch (JsonProcessingException e) {
            throw new RuntimeException(e);
        }
    }

    private static TestQuerySuite<?> readQueries() {
        return readFileAs("spark_queries.json", new TypeReference<>() {});
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
        queryAndValidate(query, TABLE);
    }

    private void queryAndValidate(TestQuery<?> query, String tableName) {
        final String sql = query.getSql().replace(TestQuerySuite.TABLE_PLACEHOLDER, tableName);
        logger.info("Performing query with spark: {}", sql);
        query.getExpectedQueryResult().validateResult(spark.sql(sql), config.getTestContext());
    }

    private String curatedTable(String baseTableName) {
        return baseTableName + "_curated";
    }

    private String latestTable(String baseTableName) {
        return baseTableName + "_latest";
    }

    private String diagnosisTable(String baseTableName) {
        return baseTableName + "_dx";
    }

    private static String newTable() {
        return "testdata" + System.currentTimeMillis();
    }

}
