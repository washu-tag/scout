from typing import List, Optional

from delta import DeltaTable
from pyspark.sql.types import StructType, StructField, StringType, BooleanType
from temporalio import activity

from .mappingentry import MappingEntry
from .sparkutils import (
    filter_df_for_update_inserts,
    merge_df_into_dt_on_column,
    extract_from_anticipated_column,
)

from pyspark.sql import functions as F, Column, DataFrame, Window, SparkSession
import uuid

mapping_schema = StructType(
    [
        StructField("scout_patient_id", StringType(), True),
        StructField("primary_report_identifier", StringType(), True),
        StructField("mpi", StringType(), True),
        StructField("epic_mrn", StringType(), True),
        StructField("consistent", BooleanType(), True),
    ]
)


def create_exact_match_condition(df1: DataFrame, df2: DataFrame) -> Column:
    return (df1["mpi"].eqNullSafe(df2["mpi"])) & (
        df1["epic_mrn"].eqNullSafe(df2["epic_mrn"])
    )


def search_mappings_on_patient_id(
    cases: List[MappingEntry], mpi: str = None, epic_mrn: str = None
) -> List[MappingEntry]:
    return [
        mapping
        for mapping in cases
        if (mpi is not None and mpi == mapping.mpi)
        or (epic_mrn is not None and epic_mrn == mapping.epic_mrn)
    ]


def search_existing_on_patient_id(
    existing_mapping_df: DataFrame, mpi: str = None, epic_mrn: str = None
):
    search_condition = F.lit(False)
    if mpi is not None:
        search_condition = search_condition | (F.col("mpi") == mpi)
    if epic_mrn is not None:
        search_condition = search_condition | (F.col("epic_mrn") == epic_mrn)
    return existing_mapping_df.filter(search_condition)


class MappingTableExtractor:
    def __init__(self, spark: SparkSession, table_name: str):
        self.spark = spark
        self.table_name = table_name
        self.existing_mapping_df: Optional[DataFrame] = None
        self.deferred_reports_df: Optional[DataFrame] = None
        self.dataframes_to_unpersist: List[DataFrame] = []

    def extract(self, batch_df: DataFrame):
        filtered_df = filter_df_for_update_inserts(batch_df)
        if filtered_df is None:
            return
        processed_df = self.preprocess(filtered_df)
        df = self.process_stage_1(processed_df)
        df = self.process_stage_2(df)
        df = self.process_stage_3(df)
        self.process_stage_4(df)
        self.process_stage_5()
        self.postprocess()

    def cache(self, df: DataFrame) -> DataFrame:
        cached_df = df.cache()
        self.dataframes_to_unpersist.append(cached_df)
        return cached_df

    def recache_existing_mapping(self):
        if self.existing_mapping_df is not None:
            self.existing_mapping_df.unpersist()
        self.existing_mapping_df = self.cache(self.spark.read.table(self.table_name))
        activity.logger.info("Updated existing mapping table reread")

    def merge_to_dt(self, df: DataFrame):
        merge_df_into_dt_on_column(
            DeltaTable.forName(self.spark, self.table_name),
            df,
            "primary_report_identifier",
            False,
        )

    def preprocess(self, filtered_df: DataFrame) -> DataFrame:
        mapping_exists = self.spark.catalog.tableExists(self.table_name)
        df = filtered_df

        if mapping_exists:
            self.recache_existing_mapping()
            df = filtered_df.join(
                self.existing_mapping_df.select("primary_report_identifier"),
                on="primary_report_identifier",  # don't need to process again
                how="left_anti",
            )
        else:
            self.existing_mapping_df = self.spark.createDataFrame([], mapping_schema)
            (
                DeltaTable.createIfNotExists(self.spark)
                .tableName(self.table_name)
                .addColumns(mapping_schema)
                .property("delta.enableChangeDataFeed", "true")
                .execute()
            )

        df = df.withColumns(
            {
                "resolved_mpi": F.when(
                    F.col("version_id") == "2.7",
                    extract_from_anticipated_column("empi_mr", filtered_df),
                )
                .when(
                    F.col("version_id") == "2.4",
                    F.coalesce(
                        *[
                            extract_from_anticipated_column(
                                f"{authority}_ee", filtered_df
                            )
                            for authority in ["bjh", "bjwc", "slch"]
                        ]
                    ),
                )
                .otherwise(F.col("mpi")),
                "resolved_epic_mrn": F.when(
                    F.col("version_id") == "2.7",
                    F.coalesce(
                        *[
                            extract_from_anticipated_column(id, filtered_df)
                            for id in ["epic_mrn", "mbmc_mr"]
                        ]
                    ),
                ).otherwise(F.lit(None)),
            }
        ).select(
            "primary_report_identifier",
            F.col("resolved_mpi").alias("mpi"),
            F.col("resolved_epic_mrn").alias("epic_mrn"),
        )

        return self.cache(df)

    def process_stage_1(self, df: DataFrame) -> DataFrame:
        """
        Current DataFrame status: all incoming reports, other than reports we have already seen in a previous run.
        Goal: Find reports we can defer until the end to update in a bulk action defined by:
            1. Any incoming report with an exact match (both `mpi` and `epic_mrn`) in the mapping table already, OR
            2. Any incoming report with duplicated IDs among the rest of the incoming reports _except_ the first report.
        :param df: DataFrame of data to be processed
        """

        exact_match_condition = create_exact_match_condition(
            df, self.existing_mapping_df
        )

        exact_matches_df = df.join(
            self.existing_mapping_df, on=exact_match_condition, how="left_semi"
        )
        remaining_incoming_reports_df = df.join(
            self.existing_mapping_df, on=exact_match_condition, how="left_anti"
        )

        incoming_report_dupe_id_window = Window.partitionBy("mpi", "epic_mrn").orderBy(
            F.monotonically_increasing_id()
        )  # gather each combo of ids
        remaining_reports_ranked = self.cache(
            remaining_incoming_reports_df.withColumn(
                "_rank", F.row_number().over(incoming_report_dupe_id_window)
            )
        )

        unique_ids_incoming_reports = self.cache(
            remaining_reports_ranked.filter(F.col("_rank") == 1).drop("_rank")
        )
        duplicate_ids_incoming_reports = remaining_reports_ranked.filter(
            F.col("_rank") > 1
        ).drop("_rank")

        self.deferred_reports_df = self.cache(
            exact_matches_df.unionByName(
                duplicate_ids_incoming_reports
            ).dropDuplicates()
        )

        activity.logger.info("Stage 1 completed on mapping table derivation")

        return unique_ids_incoming_reports

    def process_stage_2(self, df: DataFrame) -> DataFrame:
        """
        Current DataFrame status:
            * Every report incoming has a unique ID combination
            * There are no exact matches of ID combinations between the incoming reports and the mapping table (or themselves)
        Goal: Create "consistent" rows for reports satisfying, with respect to `mpi` and `epic_mrn`:
            1. Are fully disjoint from the existing mapping table AND
            2. Are fully disjoint from the rest of the incoming reports
        :param df: DataFrame of data to be processed
        """

        mpi_matches_df = df.join(
            self.existing_mapping_df.select("mpi").withColumn(
                "_contains_match", F.lit(True)
            ),
            on="mpi",
            how="left",
        )

        epic_mrn_matches_df = df.join(
            self.existing_mapping_df.select("epic_mrn").withColumn(
                "_contains_match", F.lit(True)
            ),
            on="epic_mrn",
            how="left",
        )

        partial_match_with_indicator_df = self.cache(
            mpi_matches_df.unionByName(epic_mrn_matches_df)
            .groupBy("primary_report_identifier", "mpi", "epic_mrn")
            .agg(F.max("_contains_match").alias("_contains_match"))
        )  # cache join once

        partial_existing_mapping_match_df = self.cache(
            partial_match_with_indicator_df.filter(F.col("_contains_match")).drop(
                "_contains_match"
            )
        )

        no_existing_mapping_match_df = self.cache(
            partial_match_with_indicator_df.filter(
                F.col("_contains_match").isNull()
            ).drop("_contains_match")
        )

        mpi_counts = self.cache(
            no_existing_mapping_match_df.filter(F.col("mpi").isNotNull())
            .groupBy("mpi")
            .agg(F.count("*").alias("mpi_count"))
        )
        epic_mrn_counts = self.cache(
            no_existing_mapping_match_df.filter(F.col("epic_mrn").isNotNull())
            .groupBy("epic_mrn")
            .agg(F.count("*").alias("epic_mrn_count"))
        )

        def filter_no_existing_mapping_df(additional_filter: Column) -> DataFrame:
            return (
                no_existing_mapping_match_df.join(mpi_counts, on="mpi", how="left")
                .join(epic_mrn_counts, on="epic_mrn", how="left")
                .fillna(0, subset=["mpi_count", "epic_mrn_count"])
                .filter(additional_filter)
                .drop("mpi_count", "epic_mrn_count")
            )

        fully_disjoint_reports_df = self.cache(
            filter_no_existing_mapping_df(
                (F.col("mpi_count") <= 1) & (F.col("epic_mrn_count") <= 1)
            )
        )

        fully_disjoint_count = fully_disjoint_reports_df.count()
        activity.logger.info(
            "Calculated fully disjoint reports: %d", fully_disjoint_count
        )

        incoming_reports_with_links_df = self.cache(
            filter_no_existing_mapping_df(
                (F.col("mpi_count") > 1) | (F.col("epic_mrn_count") > 1)
            )
        )
        activity.logger.info(
            "Calculated stage 2 incoming reports with links: %d",
            incoming_reports_with_links_df.count(),
        )

        if fully_disjoint_count > 0:
            activity.logger.info(
                "Inserting mapping for %d fully disjoint reports",
                fully_disjoint_count,
            )
            fully_disjoint_reports_transformed_df = fully_disjoint_reports_df.select(
                F.expr("uuid()").alias("scout_patient_id"),
                "primary_report_identifier",
                "mpi",
                "epic_mrn",
                F.lit(True).alias("consistent"),
            )

            self.merge_to_dt(fully_disjoint_reports_transformed_df)
            activity.logger.info("Fully disjoint reports inserted")
            self.recache_existing_mapping()

        activity.logger.info(
            "Creating df as union of %d incoming links and %d partial existing matches",
            incoming_reports_with_links_df.count(),
            partial_existing_mapping_match_df.count(),
        )
        remaining_reports_df = self.cache(
            incoming_reports_with_links_df.unionByName(
                partial_existing_mapping_match_df
            ).dropDuplicates()
        )

        activity.logger.info("Stage 2 completed on mapping table derivation")
        return remaining_reports_df

    def process_stage_3(self, df: DataFrame) -> DataFrame:
        """
        Current DataFrame status:
            * Every report incoming has a unique ID combination
            * There are no exact matches of ID combinations between the incoming reports and the mapping table (or themselves)
            * Every report in the incoming batch must have a partial ID match to another report, either in the batch or already in the mapping table
        Goal: Find rows with only one of `mpi` or `epic_mrn` specified and process:
            * For such reports with a partial match in the mapping table already, create a new row from our report, inheriting the `scout_patient_id` and `consistent` flag of the partial match
            * For such reports without a partial match, create a new consistent row in the mapping table
        Return remaining reports for further stages
        :param df: DataFrame of data to be processed
        """

        exactly_one_id_specified_condition = (
            F.col("mpi").isNull() != F.col("epic_mrn").isNull()
        )
        reports_with_single_id_df = self.cache(
            df.filter(exactly_one_id_specified_condition)
        )

        def process_single_id_reports(field: str):
            return (
                reports_with_single_id_df.alias("incoming")
                .join(
                    self.existing_mapping_df.alias("existing"),
                    on=field,
                    how="inner",
                )
                .select(
                    "existing.scout_patient_id",
                    "incoming.primary_report_identifier",
                    "incoming.mpi",
                    "incoming.epic_mrn",
                    "existing.consistent",
                )
            )

        single_id_reports_with_mpi_match_df = process_single_id_reports("mpi")

        single_id_reports_with_epic_mrn_match_df = process_single_id_reports("epic_mrn")

        reports_with_partial_existing_match_df = self.cache(
            single_id_reports_with_mpi_match_df.unionByName(
                single_id_reports_with_epic_mrn_match_df
            ).dropDuplicates(["primary_report_identifier"])
        )

        self.merge_to_dt(reports_with_partial_existing_match_df)

        activity.logger.info(
            "Merged %s mapping records with partial existing matches",
            reports_with_partial_existing_match_df.count(),
        )

        reports_with_no_existing_match_df = self.cache(
            reports_with_single_id_df.join(
                reports_with_partial_existing_match_df.select(
                    "primary_report_identifier"
                ),
                on="primary_report_identifier",
                how="left_anti",
            ).select(
                F.expr("uuid()").alias("scout_patient_id"),
                "primary_report_identifier",
                "mpi",
                "epic_mrn",
                F.lit(True).alias("consistent"),
            )
        )

        self.merge_to_dt(reports_with_no_existing_match_df)

        activity.logger.info(
            "Merged %s mapping records with no partial existing matches",
            reports_with_no_existing_match_df.count(),
        )

        activity.logger.info("Stage 3 completed on mapping table derivation")

        return (
            df.filter(~exactly_one_id_specified_condition)
            .withColumn("scout_patient_id", F.lit(None).cast(StringType()))
            .withColumn("consistent", F.lit(True))
        )

    def process_stage_4(self, df: DataFrame):
        """
        Current DataFrame status:
            * Every report incoming has a unique ID combination
            * There are no exact matches of ID combinations between the incoming reports and the mapping table (or themselves)
            * Every report in the incoming batch must have a partial ID match to another report, either in the batch or already in the mapping table
            * Every report in the incoming batch has both a non-null `mpi` and `epic_mrn`
        Goal: Recursive search to process all remaining reports
        :param df: DataFrame of data to be processed
        """

        complex_cases = [MappingEntry.from_df_row(row) for row in df.collect()]
        bulk_updates = []
        activity.logger.info(
            "Performing recursive search to resolve IDs for %d reports",
            len(complex_cases),
        )
        while len(complex_cases) > 0:
            complex_case = complex_cases.pop()
            known_mpis = []
            known_mrns = []
            patient_web = self.recursive_search_patient_web(
                complex_cases,
                known_mpis,
                known_mrns,
                [complex_case],
                complex_case,
            )
            unique_ids = list(
                dict.fromkeys(
                    entry.scout_patient_id
                    for entry in patient_web
                    if entry.scout_patient_id is not None
                )
            )
            generated_uuid = (
                unique_ids[0] if len(unique_ids) > 0 else str(uuid.uuid4())
            )  # take the only ID, generating a new one if none exist
            if (len(known_mpis) > 1) or (len(known_mrns) > 1):
                activity.logger.info(
                    "Inconsistent patient IDs found, marking all linked report mappings"
                )
                for entry in patient_web:
                    if entry.consistent or entry.scout_patient_id != generated_uuid:
                        entry.scout_patient_id = generated_uuid
                        entry.consistent = False
                        bulk_updates.append(entry)
                # TODO: history table
            else:
                if len(unique_ids) < 2:
                    for entry in patient_web:
                        if (
                            not entry.existing_mapping
                        ):  # we only need to add new mapping entries
                            entry.scout_patient_id = generated_uuid
                            bulk_updates.append(entry)
                else:  # IDs are still consistent, but we must perform a patient merge
                    generated_uuid = unique_ids[0]
                    for entry in patient_web:
                        if entry.scout_patient_id != generated_uuid:
                            entry.scout_patient_id = generated_uuid
                            bulk_updates.append(entry)
                            # TODO: history table
            activity.logger.info(
                "Remaining reports for recursive search: %d", len(complex_cases)
            )
        self.merge_to_dt(
            self.spark.createDataFrame(
                [mapping.to_dict() for mapping in bulk_updates],
                mapping_schema,
            )
        )

    def recursive_search_patient_web(
        self,
        new_mapping_search_space: List[MappingEntry],
        known_mpis: List[str],
        known_epic_mrns: List[str],
        partial_search_results: List[MappingEntry],
        current_case: MappingEntry,
    ) -> List[MappingEntry]:
        new_mpi_revealed = None
        new_mrn_revealed = None
        if current_case.mpi is not None and not (current_case.mpi in known_mpis):
            new_mpi_revealed = current_case.mpi
            known_mpis.append(new_mpi_revealed)
        if current_case.epic_mrn is not None and not (
            current_case.epic_mrn in known_epic_mrns
        ):
            new_mrn_revealed = current_case.epic_mrn
            known_epic_mrns.append(new_mrn_revealed)
        if new_mpi_revealed is None and new_mrn_revealed is None:
            return partial_search_results
        new_incoming_mapping_finds = search_mappings_on_patient_id(
            new_mapping_search_space, new_mpi_revealed, new_mrn_revealed
        )

        partial_search_results.extend(new_incoming_mapping_finds)
        new_mapping_search_space[:] = [
            x for x in new_mapping_search_space if x not in new_incoming_mapping_finds
        ]

        for case in new_incoming_mapping_finds:
            self.recursive_search_patient_web(
                new_mapping_search_space,
                known_mpis,
                known_epic_mrns,
                partial_search_results,
                case,
            )

        new_existing_mapping_finds_df = search_existing_on_patient_id(
            self.existing_mapping_df, new_mpi_revealed, new_mrn_revealed
        ).cache()  # we're unpersisting early here
        if new_existing_mapping_finds_df.count() > 0:
            new_existing_mapping_finds = [
                MappingEntry.from_df_row(row, True)
                for row in new_existing_mapping_finds_df.collect()
            ]
            new_existing_mapping_finds = [
                find
                for find in new_existing_mapping_finds
                if find not in partial_search_results
            ]
            partial_search_results.extend(new_existing_mapping_finds)
            for case in new_existing_mapping_finds:
                self.recursive_search_patient_web(
                    new_mapping_search_space,
                    known_mpis,
                    known_epic_mrns,
                    partial_search_results,
                    case,
                )

        new_existing_mapping_finds_df.unpersist()

        return partial_search_results

    def process_stage_5(self):
        """
        Goal: Process earlier deferred reports
        """

        deferred_report_count = self.deferred_reports_df.count()
        activity.logger.info(
            "Beginning stage 5: adding back in %d reports with guaranteed existing matches",
            deferred_report_count,
        )

        if deferred_report_count > 0:
            self.recache_existing_mapping()

            dupes_to_add_df = (
                self.deferred_reports_df.alias("incoming")
                .join(
                    self.existing_mapping_df.alias("existing"),
                    on=create_exact_match_condition(
                        self.deferred_reports_df, self.existing_mapping_df
                    ),
                    how="inner",
                )
                .select(
                    "existing.scout_patient_id",
                    "incoming.primary_report_identifier",  # take only the report ID from the incoming data
                    "existing.mpi",  # guaranteed a match, take everything from the match other than the report ID
                    "existing.epic_mrn",
                    "existing.consistent",
                )
                .dropDuplicates(["primary_report_identifier"])
            )

            self.merge_to_dt(dupes_to_add_df)

    def postprocess(self):
        for df in self.dataframes_to_unpersist:
            df.unpersist()
        activity.logger.info("Mapping table derivation complete")
