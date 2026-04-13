package edu.washu.tag.tests;

import static org.assertj.core.api.Assertions.assertThat;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import edu.washu.tag.BaseTest;
import edu.washu.tag.TestQuery;
import edu.washu.tag.TestQuerySuite;
import edu.washu.tag.model.IngestJobInput;
import edu.washu.tag.model.ReportPatientMappingEntry;
import edu.washu.tag.util.FileIOUtils;
import edu.washu.tag.validation.ExactNumberObjectsResult;
import edu.washu.tag.validation.ExactRowsResult;
import edu.washu.tag.validation.column.ArrayType;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
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
    private static final ObjectMapper objectMapper = new ObjectMapper();
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
            for (String id : Arrays.asList("mpi", "slch_mr", "bjwc_mr", "bjh_mr", "epic_mrn", "mbmc_mr", "patient_mpi")) {
                map.put(id, null);
            }
            return map;
        };
        final String patientIdColumn = "primary_patient_identifier";

        final Map<String, String> legacyReport = baseAssertionMap.apply("1.2.3.4.5");
        legacyReport.put("mpi", "999999-001");
        legacyReport.put(patientIdColumn, "mpi_999999-001");
        legacyReport.put("patient_mpi", "999999-001");

        final Map<String, String> slchReport = baseAssertionMap.apply("1.2.3.4.6");
        slchReport.put("slch_mr", "999999-S001");
        slchReport.put(patientIdColumn, "slch_mr_999999-S001");
        slchReport.put("patient_ids", "[[999999-W126,SLCH,EE,null], [999999-S001,SLCH,MR,null]]");
        slchReport.put("patient_mpi", "999999-W126");

        final Map<String, String> bjwcReport = baseAssertionMap.apply("1.2.3.4.7");
        bjwcReport.put("bjwc_mr", "999999-B001");
        bjwcReport.put(patientIdColumn, "bjwc_mr_999999-B001");
        bjwcReport.put("patient_ids", "[[999999-W127,BJWC,EE,null], [999999-B001,BJWC,MR,null]]");
        bjwcReport.put("patient_mpi", "999999-W127");

        final Map<String, String> bjhReport = baseAssertionMap.apply("1.2.3.4.8");
        bjhReport.put("bjh_mr", "999999-H001");
        bjhReport.put(patientIdColumn, "bjh_mr_999999-H001");
        bjhReport.put("patient_ids", "[[999999-W128,BJH,EE,null], [999999-H001,BJH,MR,null]]");
        bjhReport.put("patient_mpi", "999999-W128");

        final Map<String, String> epicReport = baseAssertionMap.apply("1.2.3.4.9");
        epicReport.put("epic_mrn", "999999-EPIC0001");
        epicReport.put("empi_mr", "999999-001");
        epicReport.put(patientIdColumn, "epic_mrn_999999-EPIC0001");
        epicReport.put("patient_ids", "[[999999-001,EMPI,MR,null], [999999-EPIC0001,EPIC,MRN,null]]");
        epicReport.put("patient_mpi", "999999-001");

        final Map<String, String> mbmcReport = baseAssertionMap.apply("1.2.3.4.10");
        mbmcReport.put("mbmc_mr", "999999-M00001");
        mbmcReport.put(patientIdColumn, "mbmc_mr_999999-M00001");
        mbmcReport.put("patient_ids", "[[999999-M00001,MBMC,MR,null]]");
        mbmcReport.put("patient_mpi", null);

        final ExactRowsResult expected = new ExactRowsResult();
        expected.setRowAssertions(rowAssertions);
        expected.setUniqueIdColumnName(COLUMN_MESSAGE_CONTROL_ID);
        expected.setColumnTypes(Collections.singleton(new ArrayType("patient_ids")));
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
        final Set<String> latestReportIds = Set.of("1.2.4.8.16.1", "1.2.4.8.16.2", "1.2.4.8.16.4", "1.2.4.8.16.6", "1.2.4.8.16.7");

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
            "filler_order_number",
            "bjh_ss",
            "bjwc_ss",
            "slch_ss"
        ));
    }

    @Test
    public void testLongitudinalPatientIds() {
        final String baseTableName = newTable();
        final String mappingTableName = baseTableName + "_report_patient_mapping";
        ingest(
            new IngestJobInput().setReportTableName(baseTableName).setLogPaths("/data/transitive_id_resolution/20260329.log")
        );

        final MappingLookup report0 = new MappingLookup(29, 0, "LTI_01", null);
        final MappingLookup report1 = new MappingLookup(29, 1, "LTI_02", null);
        final MappingLookup report2 = new MappingLookup(29, 2, "LTI_02", null);
        final MappingLookup report3 = new MappingLookup(29, 3, null, "LTI_03");
        final MappingLookup report4 = new MappingLookup(29, 4, "LTI_04", null);
        final MappingLookup report5 = new MappingLookup(29, 5, "LTI_04", null);
        final MappingLookup report6 = new MappingLookup(29, 6, null, "LTI_05");
        final MappingLookup report7 = new MappingLookup(29, 7, "LTI_06", "LTI_07");
        final MappingLookup report8 = new MappingLookup(29, 8, "LTI_08", "LTI_09");
        final MappingLookup report9 = new MappingLookup(29, 9, "LTI_10", "LTI_11");
        final MappingLookup report10 = new MappingLookup(29, 10, "LTI_10", "LTI_12");
        final MappingLookup report11 = new MappingLookup(29, 11, "LTI_13", null);
        final MappingLookup report12 = new MappingLookup(29, 12, "LTI_13", "LTI_14");

        final List<ExpectedPatientCluster> expectedPatients = new ArrayList<>();
        expectedPatients.add(new ExpectedPatientCluster(true, report0));
        expectedPatients.add(new ExpectedPatientCluster(true, report1, report2));
        expectedPatients.add(new ExpectedPatientCluster(true, report3));
        expectedPatients.add(new ExpectedPatientCluster(true, report4, report5));
        expectedPatients.add(new ExpectedPatientCluster(true, report6));
        expectedPatients.add(new ExpectedPatientCluster(true, report7));
        expectedPatients.add(new ExpectedPatientCluster(true, report8));
        expectedPatients.add(new ExpectedPatientCluster(false, report9, report10));
        expectedPatients.add(new ExpectedPatientCluster(true, report11, report12));

        validateMappingTable(mappingTableName, expectedPatients);

        ingest(
            new IngestJobInput().setReportTableName(baseTableName).setLogPaths("/data/transitive_id_resolution/20260330.log")
        );

        final MappingLookup reportSecondDay0 = new MappingLookup(30, 0, "LTI_01", null);
        final MappingLookup reportSecondDay1 = new MappingLookup(30, 1, null, "LTI_03");
        final MappingLookup reportSecondDay2 = new MappingLookup(30, 2, "LTI_06", "LTI_07");
        final MappingLookup reportSecondDay3 = new MappingLookup(30, 3, "LTI_15", null);
        final MappingLookup reportSecondDay4 = new MappingLookup(30, 4, null, "LTI_16");
        final MappingLookup reportSecondDay5 = new MappingLookup(30, 5, "LTI_17", "LTI_18");
        final MappingLookup reportSecondDay6 = new MappingLookup(30, 6, "LTI_17", "LTI_18");
        final MappingLookup reportSecondDay7 = new MappingLookup(30, 7, "LTI_02", "LTI_03");
        final MappingLookup reportSecondDay8 = new MappingLookup(30, 8, "LTI_04", "LTI_05");
        final MappingLookup reportSecondDay9 = new MappingLookup(30, 9, "LTI_01", "LTI_05");
        final MappingLookup reportSecondDay10 = new MappingLookup(30, 10, "LTI_19", "LTI_14");

        expectedPatients.clear();
        expectedPatients.add(new ExpectedPatientCluster(false, report0, reportSecondDay0, reportSecondDay9, report6, reportSecondDay8, report4, report5));
        expectedPatients.add(new ExpectedPatientCluster(true, report3, reportSecondDay1, reportSecondDay7, report1, report2));
        expectedPatients.add(new ExpectedPatientCluster(true, report7, reportSecondDay2));
        expectedPatients.add(new ExpectedPatientCluster(true, reportSecondDay3));
        expectedPatients.add(new ExpectedPatientCluster(true, reportSecondDay4));
        expectedPatients.add(new ExpectedPatientCluster(true, reportSecondDay5, reportSecondDay6));
        expectedPatients.add(new ExpectedPatientCluster(true, report8));
        expectedPatients.add(new ExpectedPatientCluster(false, report9, report10));
        expectedPatients.add(new ExpectedPatientCluster(false, report11, report12, reportSecondDay10));

        validateMappingTable(mappingTableName, expectedPatients);

        final String curatedEpicView = baseTableName + "_curated_epic_view";

        final TestQuery<?> viewSizeQuery = new TestQuery<>("viewSizeQuery", "SELECT * FROM " + curatedEpicView);
        final ExactNumberObjectsResult viewCount = new ExactNumberObjectsResult();
        viewCount.setExpectedNumResults(
            expectedPatients
                .stream()
                .filter(ExpectedPatientCluster::consistent)
                .mapToInt(cluster -> cluster.expectedRows().length)
                .sum()
        );
        viewSizeQuery.setExpectedQueryResult(viewCount);
        queryAndValidate(viewSizeQuery, curatedEpicView);

        // Now let's check the view. The view should return a number of rows equal to report count total in consistent clusters
        // test queries to run:
        // 1) EPIC MRN that exists but inconsistent should return no rows
        // 2) EPIC MRN that exists and is consistent should return full patient web.
        // Do both queries in 1 spark SQL call. This is closest to real user interaction where the user
        // may request a mix of consistent and inconsistent EPIC MRNs

        final String expectedPatientId = spark.sql(String.format("SELECT scout_patient_id FROM %s WHERE message_control_id='1.2.3.29.1'", curatedEpicView))
            .collectAsList()
            .getFirst()
            .getString(0);
        final Map<String, Map<String, String>> rowAssertions = new HashMap<>();
        final Map<String, String> report1ViewRepresentation = new HashMap<>();
        report1ViewRepresentation.put("mpi", null);
        report1ViewRepresentation.put("bjh_ee", "LTI_02");
        report1ViewRepresentation.put("epic_mrn", null);
        report1ViewRepresentation.put("version_id", "2.4");
        report1ViewRepresentation.put("sending_facility", "ABCHOSP1");
        report1ViewRepresentation.put("primary_report_identifier", "s3://%LAKE_BUCKET%/hl7/2026/20260329.zip/2026/03/29/16/202603291659521322_1.hl7");
        rowAssertions.put("1.2.3.29.1", report1ViewRepresentation);

        final Map<String, String> report2ViewRepresentation = new HashMap<>();
        report2ViewRepresentation.put("mpi", null);
        report2ViewRepresentation.put("bjwc_ee", "LTI_02");
        report2ViewRepresentation.put("epic_mrn", null);
        report2ViewRepresentation.put("version_id", "2.4");
        report2ViewRepresentation.put("sending_facility", "ABCHOSP2");
        report2ViewRepresentation.put("primary_report_identifier", "s3://%LAKE_BUCKET%/hl7/2026/20260329.zip/2026/03/29/16/202603291659538535_2.hl7");
        rowAssertions.put("1.2.3.29.2", report2ViewRepresentation);

        final Map<String, String> report3ViewRepresentation = new HashMap<>();
        report3ViewRepresentation.put("mpi", null);
        report3ViewRepresentation.put("epic_mrn", "LTI_03");
        report3ViewRepresentation.put("version_id", "2.7");
        report3ViewRepresentation.put("sending_facility", "ABCHOSP3");
        report3ViewRepresentation.put("primary_report_identifier", "s3://%LAKE_BUCKET%/hl7/2026/20260329.zip/2026/03/29/16/202603291659544492_3.hl7");
        rowAssertions.put("1.2.3.29.3", report3ViewRepresentation);

        final Map<String, String> reportSecondDay1ViewRepresentation = new HashMap<>();
        reportSecondDay1ViewRepresentation.put("mpi", null);
        reportSecondDay1ViewRepresentation.put("epic_mrn", "LTI_03");
        reportSecondDay1ViewRepresentation.put("version_id", "2.7");
        reportSecondDay1ViewRepresentation.put("sending_facility", "ABCHOSPD21");
        reportSecondDay1ViewRepresentation.put("primary_report_identifier", "s3://%LAKE_BUCKET%/hl7/2026/20260330.zip/2026/03/30/16/202603301659521322_1.hl7");
        rowAssertions.put("1.2.3.30.1", reportSecondDay1ViewRepresentation);

        final Map<String, String> reportSecondDay7ViewRepresentation = new HashMap<>();
        reportSecondDay7ViewRepresentation.put("mpi", null);
        reportSecondDay7ViewRepresentation.put("empi_mr", "LTI_02");
        reportSecondDay7ViewRepresentation.put("epic_mrn", "LTI_03");
        reportSecondDay7ViewRepresentation.put("version_id", "2.7");
        reportSecondDay7ViewRepresentation.put("sending_facility", "ABCHOSPD27");
        reportSecondDay7ViewRepresentation.put("primary_report_identifier", "s3://%LAKE_BUCKET%/hl7/2026/20260330.zip/2026/03/30/16/202603301659551654_7.hl7");
        rowAssertions.put("1.2.3.30.7", reportSecondDay7ViewRepresentation);

        for (Map<String, String> assertions : rowAssertions.values()) {
            assertions.put("scout_patient_id", expectedPatientId);
            assertions.put("resolved_mpi", "LTI_02");
            assertions.put("resolved_epic_mrn", "LTI_03");
        }
        final TestQuery<?> viewResolvedQuery = new TestQuery<>(
            "viewResolvedQuery",
            "SELECT * FROM " + curatedEpicView + " WHERE resolved_epic_mrn IN ('LTI_03', 'LTI_05', 'LTI_11')"
        );
        final ExactRowsResult viewResolvedQueryResult = new ExactRowsResult();
        viewResolvedQuery.setExpectedQueryResult(viewResolvedQueryResult);
        viewResolvedQueryResult.setUniqueIdColumnName("message_control_id");
        viewResolvedQueryResult.setRowAssertions(rowAssertions);
        queryAndValidate(viewResolvedQuery, curatedEpicView);
    }

    private List<ReportPatientMappingEntry> readMappingTable(String tableName) {
        return spark.sql("SELECT * FROM " + tableName)
            .toJSON()
            .collectAsList()
            .stream()
            .map(row -> {
                try {
                    return objectMapper.readValue(row, ReportPatientMappingEntry.class);
                } catch (JsonProcessingException e) {
                    throw new RuntimeException(e);
                }
            }).toList();
    }

    private ReportPatientMappingEntry findExpectedMapping(List<ReportPatientMappingEntry> mappingsToSearch, MappingLookup expectedMapping) {
        return mappingsToSearch.stream()
            .filter(mapping -> {
                final String reportId = mapping.getPrimaryReportIdentifier();
                return reportId.contains(String.format("202603%d.zip", expectedMapping.day))
                    && reportId.contains(String.format("_%d.hl7", expectedMapping.index));
            })
            .findFirst()
            .orElseThrow(RuntimeException::new);
    }

    private void validateMappingTable(String tableName, List<ExpectedPatientCluster> expectedPatientClusters) {
        final List<ReportPatientMappingEntry> actualState = readMappingTable(tableName);

        assertThat(actualState).as("mapping table").hasSize(
            expectedPatientClusters.stream().mapToInt(cluster -> Math.toIntExact(Arrays.stream(cluster.expectedRows()).count())).sum()
        );
        assertSubsetOfMappingsHasExactlyNumberOfScoutIds(actualState, expectedPatientClusters.size());

        for (ExpectedPatientCluster expectedPatientCluster : expectedPatientClusters) {
            final MappingLookup lookup = expectedPatientCluster.expectedRows()[0];
            final String scoutIdForPatient = findExpectedMapping(actualState, lookup).getScoutPatientId();

            final List<ReportPatientMappingEntry> mappingsWithMatchingScoutId = actualState.stream()
                .filter(mapping -> mapping.getScoutPatientId().equals(scoutIdForPatient))
                .toList();

            final int expectedNumRowsForPatient = expectedPatientCluster.expectedRows().length;
            assertThat(
                mappingsWithMatchingScoutId
                    .stream()
                    .map(ReportPatientMappingEntry::getPrimaryReportIdentifier)
                    .collect(Collectors.toSet())
            ).as("subset of mapping table").hasSize(expectedNumRowsForPatient);
            logger.info("Checked mapping table subset to have size {}", expectedNumRowsForPatient);
            for (MappingLookup expectedMapping : expectedPatientCluster.expectedRows()) {
                final ReportPatientMappingEntry actualMapping = findExpectedMapping(mappingsWithMatchingScoutId, expectedMapping);

                assertThat(actualMapping.getScoutPatientId())
                    .as("Scout ID of mapping row")
                    .isEqualTo(scoutIdForPatient);

                assertThat(actualMapping.isConsistent())
                    .as("consistent flag on mapping row")
                    .isEqualTo(expectedPatientCluster.consistent());

                assertThat(actualMapping.getMpi())
                    .as("mpi on mapping row")
                    .isEqualTo(expectedMapping.mpi);

                assertThat(actualMapping.getEpicMrn())
                    .as("EPIC MRN on mapping row")
                    .isEqualTo(expectedMapping.epicMrn);

                logger.info("Validated mapping for row {}", actualMapping.getPrimaryReportIdentifier());
            }
            logger.info("Validated mappings associated to scout patient id {}", scoutIdForPatient);
        }
    }

    private void assertSubsetOfMappingsHasExactlyNumberOfScoutIds(List<ReportPatientMappingEntry> mappingSubset, int expectedSize) {
        assertThat(
            mappingSubset
                .stream()
                .map(ReportPatientMappingEntry::getScoutPatientId)
                .collect(Collectors.toSet())
        ).as("subset of mapping table").hasSize(expectedSize);
        logger.info("Checked mapping table subset to have {} unique Scout patient IDs", expectedSize);
    }

    private static <T> T readFileAs(String resourceName, TypeReference<T> classObj) {
        try {
            return objectMapper.readValue(
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

    private record MappingLookup(int day, int index, String mpi, String epicMrn) {

    }

    private record ExpectedPatientCluster(boolean consistent, MappingLookup... expectedRows) {

    }

}
