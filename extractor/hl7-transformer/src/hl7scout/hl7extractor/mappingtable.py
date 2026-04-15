from typing import List

from delta import DeltaTable
from pyspark.sql.types import StructType, StructField, StringType, BooleanType
from temporalio import activity

from .derivativetable import DerivativeTable
from .mappingentry import MappingEntry
from .sparkutils import (
    filter_df_for_update_inserts,
    merge_df_into_dt_on_column,
    extract_from_anticipated_column,
)

from pyspark.sql import functions as F, Column, DataFrame, Window, SparkSession
import uuid


def create_exact_match_condition(df1: DataFrame, df2: DataFrame) -> Column:
    return (df1["mpi"].eqNullSafe(df2["mpi"])) & (
        df1["epic_mrn"].eqNullSafe(df2["epic_mrn"])
    )


def copy_existing_assumed_matches_into_incoming_reports(
    incoming_df: DataFrame, existing_mapping_df: DataFrame, join_condition: Column
) -> DataFrame:
    return (
        incoming_df.alias("incoming")
        .join(
            existing_mapping_df.alias("existing"),
            on=join_condition,
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


def mapping_table(base_report_table_name: str) -> DerivativeTable:
    source_table = f"{base_report_table_name}_curated"

    def process_table(batch_df, spark, table_name):
        extract_mapping(batch_df, spark, table_name, source_table)

    return DerivativeTable(
        source_table=source_table,
        table_name=f"{base_report_table_name}_report_patient_mapping",
        process_source_data=process_table,
    )


def extract_mapping(batch_df, spark, table_name, source_table):
    filtered_df = filter_df_for_update_inserts(batch_df)
    if filtered_df is None:
        return

    mapping_exists = spark.catalog.tableExists(table_name)

    if mapping_exists:
        existing_mapping_df = spark.read.table(table_name)
        filtered_df = filtered_df.join(
            existing_mapping_df.select(
                "primary_report_identifier"
            ),  # small df for filter
            on="primary_report_identifier",  # don't need to process again
            how="left_anti",
        )
    else:
        schema = StructType(
            [
                StructField("scout_patient_id", StringType(), True),
                StructField("primary_report_identifier", StringType(), True),
                StructField("mpi", StringType(), True),
                StructField("epic_mrn", StringType(), True),
                StructField("consistent", BooleanType(), True),
            ]
        )
        existing_mapping_df = spark.createDataFrame([], schema)
        (
            DeltaTable.createIfNotExists(spark)
            .tableName(table_name)
            .addColumns(schema)
            .property("delta.enableChangeDataFeed", "true")
            .execute()
        )

    filtered_df = filtered_df.withColumns(
        {
            "resolved_mpi": F.when(
                F.col("version_id") == "2.7",
                extract_from_anticipated_column("empi_mr", filtered_df),
            )
            .when(
                F.col("version_id") == "2.4",
                F.coalesce(
                    *[
                        extract_from_anticipated_column(f"{authority}_ee", filtered_df)
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

    filtered_df.cache()
    existing_mapping_df.cache()

    exact_match_condition = create_exact_match_condition(
        filtered_df, existing_mapping_df
    )

    exact_matches_df = filtered_df.join(
        existing_mapping_df, on=exact_match_condition, how="left_semi"
    )
    remaining_incoming_reports_df = filtered_df.join(
        existing_mapping_df, on=exact_match_condition, how="left_anti"
    )

    incoming_report_dupe_id_window = Window.partitionBy("mpi", "epic_mrn").orderBy(
        F.monotonically_increasing_id()
    )  # gather each combo of ids
    remaining_reports_ranked = remaining_incoming_reports_df.withColumn(
        "_rank", F.row_number().over(incoming_report_dupe_id_window)
    ).cache()

    unique_ids_incoming_reports = (
        remaining_reports_ranked.filter(F.col("_rank") == 1).drop("_rank").cache()
    )
    duplicate_ids_incoming_reports = (
        remaining_reports_ranked.filter(F.col("_rank") > 1).drop("_rank").cache()
    )

    deferred_reports_df = (
        exact_matches_df.unionByName(duplicate_ids_incoming_reports)
        .cache()
        .dropDuplicates()
    )

    activity.logger.info("Stage 1 completed on mapping table derivation")

    # Stage 1 complete, begin stage 2
    remaining_reports_df = unique_ids_incoming_reports

    partial_match_condition = (
        remaining_reports_df["mpi"] == existing_mapping_df["mpi"]
    ) | (remaining_reports_df["epic_mrn"] == existing_mapping_df["epic_mrn"])

    partial_match_with_indicator_df = (
        remaining_reports_df.join(
            existing_mapping_df.select("mpi", "epic_mrn").withColumn(
                "_contains_match", F.lit(True)
            ),
            on=partial_match_condition,
            how="left",
        )
        .select(
            remaining_reports_df["primary_report_identifier"],
            remaining_reports_df["mpi"],
            remaining_reports_df["epic_mrn"],
            F.col("_contains_match"),
        )
        .dropDuplicates(["primary_report_identifier"])
        .cache()
    )  # cache join once

    partial_existing_mapping_match_df = (
        partial_match_with_indicator_df.filter(F.col("_contains_match"))
        .drop("_contains_match")
        .cache()
    )

    no_existing_mapping_match_df = (
        partial_match_with_indicator_df.filter(F.col("_contains_match").isNull())
        .drop("_contains_match")
        .cache()
    )

    mpi_counts = (
        no_existing_mapping_match_df.filter(F.col("mpi").isNotNull())
        .groupBy("mpi")
        .agg(F.count("*").alias("mpi_count"))
    ).cache()
    epic_mrn_counts = (
        no_existing_mapping_match_df.filter(F.col("epic_mrn").isNotNull())
        .groupBy("epic_mrn")
        .agg(F.count("*").alias("epic_mrn_count"))
    ).cache()

    def filter_no_existing_mapping_df(additional_filter: Column) -> DataFrame:
        return (
            no_existing_mapping_match_df.join(mpi_counts, on="mpi", how="left")
            .join(epic_mrn_counts, on="epic_mrn", how="left")
            .fillna(0, subset=["mpi_count", "epic_mrn_count"])
            .filter(additional_filter)
            .drop("mpi_count", "epic_mrn_count")
        )

    fully_disjoint_reports_df = filter_no_existing_mapping_df(
        (F.col("mpi_count") <= 1) & (F.col("epic_mrn_count") <= 1)
    ).cache()
    activity.logger.info("Calculated fully disjoint reports")
    incoming_reports_with_links_df = filter_no_existing_mapping_df(
        (F.col("mpi_count") > 1) | (F.col("epic_mrn_count") > 1)
    ).cache()
    activity.logger.info("Calculated stage 2 incoming reports with links")

    if not (fully_disjoint_reports_df.isEmpty()):
        activity.logger.info(
            "Inserting mapping for %d fully disjoint reports",
            fully_disjoint_reports_df.count(),
        )
        fully_disjoint_reports_transformed_df = fully_disjoint_reports_df.select(
            F.expr("uuid()").alias("scout_patient_id"),
            "primary_report_identifier",
            "mpi",
            "epic_mrn",
            F.lit(True).alias("consistent"),
        )

        merge_df_into_dt_on_column(
            DeltaTable.forName(spark, table_name),
            fully_disjoint_reports_transformed_df,
            "primary_report_identifier",
            False,
        )
        activity.logger.info("Fully disjoint reports inserted")
        existing_mapping_df.unpersist()
        existing_mapping_df = spark.read.table(table_name)
        activity.logger.info("Updated existing mapping table reread")

    activity.logger.info(
        "Creating df as union of %d incoming links and %d partial existing matches",
        incoming_reports_with_links_df.count(),
        partial_existing_mapping_match_df.count(),
    )
    remaining_reports_df = (
        incoming_reports_with_links_df.unionByName(partial_existing_mapping_match_df)
        .dropDuplicates()
        .cache()
    )

    # Stage 2 complete, begin stage 3

    activity.logger.info("Stage 2 completed on mapping table derivation")

    exactly_one_id_specified_condition = (
        F.col("mpi").isNull() != F.col("epic_mrn").isNull()
    )
    reports_with_single_id_df = remaining_reports_df.filter(
        exactly_one_id_specified_condition
    ).cache()

    partial_match_condition = (
        reports_with_single_id_df["mpi"] == existing_mapping_df["mpi"]
    ) | (reports_with_single_id_df["epic_mrn"] == existing_mapping_df["epic_mrn"])

    reports_with_partial_existing_match_df = (
        reports_with_single_id_df.alias("incoming")
        .join(
            existing_mapping_df.alias("existing"),
            on=partial_match_condition,
            how="inner",
        )
        .select(
            "existing.scout_patient_id",
            "incoming.primary_report_identifier",
            "incoming.mpi",
            "incoming.epic_mrn",
            "existing.consistent",
        )
        .dropDuplicates(["primary_report_identifier"])
    ).cache()

    merge_df_into_dt_on_column(
        DeltaTable.forName(spark, table_name),
        reports_with_partial_existing_match_df,
        "primary_report_identifier",
        False,
    )

    activity.logger.info(
        "Merged %s mapping records with partial existing matches",
        reports_with_partial_existing_match_df.count(),
    )

    reports_with_no_existing_match_df = (
        reports_with_single_id_df.join(
            existing_mapping_df,
            on=partial_match_condition,
            how="left_anti",
        ).select(
            F.expr("uuid()").alias("scout_patient_id"),
            "primary_report_identifier",
            "mpi",
            "epic_mrn",
            F.lit(True).alias("consistent"),
        )
    ).cache()

    merge_df_into_dt_on_column(
        DeltaTable.forName(spark, table_name),
        reports_with_no_existing_match_df,
        "primary_report_identifier",
        False,
    )

    activity.logger.info(
        "Merged %s mapping records with no partial existing matches",
        reports_with_no_existing_match_df.count(),
    )

    # Stage 3 complete, begin stage 4

    activity.logger.info("Stage 3 completed on mapping table derivation")

    complex_cases_df = (
        remaining_reports_df.filter(~exactly_one_id_specified_condition)
        .withColumn("scout_patient_id", F.lit(None).cast(StringType()))
        .withColumn("consistent", F.lit(True))
        .cache()
    )

    recurse_complex_cases(spark, complex_cases_df, table_name)

    # TODO: is that enough?
    activity.logger.info(
        "Beginning stage 5: adding back in reports with guaranteed existing matches"
    )

    if not (deferred_reports_df.isEmpty()):
        dupes_to_add_df = copy_existing_assumed_matches_into_incoming_reports(
            deferred_reports_df,
            existing_mapping_df,
            create_exact_match_condition(deferred_reports_df, existing_mapping_df),
        )
        merge_df_into_dt_on_column(
            DeltaTable.forName(spark, table_name),
            dupes_to_add_df,
            "primary_report_identifier",
            False,
        )

    for df in [
        no_existing_mapping_match_df,
        mpi_counts,
        epic_mrn_counts,
        filtered_df,
        fully_disjoint_reports_df,
        incoming_reports_with_links_df,
        partial_existing_mapping_match_df,
        remaining_reports_ranked,
        unique_ids_incoming_reports,
        duplicate_ids_incoming_reports,
        existing_mapping_df,
        partial_match_with_indicator_df,
        remaining_reports_df,
        reports_with_single_id_df,
        reports_with_partial_existing_match_df,
        reports_with_no_existing_match_df,
        complex_cases_df,
    ]:
        df.unpersist()
    activity.logger.info("Mapping table derivation complete")

    spark.sql(
        f"""
            CREATE OR REPLACE VIEW {source_table}_epic_view AS
            WITH patient_ids AS (
                SELECT
                    scout_patient_id,
                    MAX(epic_mrn) AS resolved_epic_mrn,
                    MAX(mpi) AS resolved_mpi
                FROM {table_name}
                WHERE consistent = true
                GROUP BY scout_patient_id
            )
            SELECT
                r.*,
                m.scout_patient_id,
                p.resolved_epic_mrn,
                p.resolved_mpi
            FROM {source_table} r
            JOIN {table_name} m
                ON r.primary_report_identifier = m.primary_report_identifier
            JOIN patient_ids p
                ON m.scout_patient_id = p.scout_patient_id
            WHERE m.consistent = true
    """
    )


def recurse_complex_cases(spark: SparkSession, df: DataFrame, table_name: str):
    complex_cases = [MappingEntry.from_df_row(row) for row in df.collect()]
    existing_mapping_df = spark.read.table(table_name).cache()
    bulk_updates = []
    activity.logger.info(
        "Performing recursive search to resolve IDs for %d reports", len(complex_cases)
    )
    while len(complex_cases) > 0:
        complex_case = complex_cases.pop()
        known_mpis = []
        known_mrns = []
        patient_web = recursive_search_patient_web(
            spark,
            complex_cases,
            existing_mapping_df,
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
    merge_df_into_dt_on_column(
        DeltaTable.forName(spark, table_name),
        spark.createDataFrame(
            [mapping.to_dict() for mapping in bulk_updates],
            StructType(
                [
                    StructField("scout_patient_id", StringType()),
                    StructField("primary_report_identifier", StringType()),
                    StructField("mpi", StringType()),
                    StructField("epic_mrn", StringType()),
                    StructField("consistent", BooleanType()),
                ]
            ),
        ),
        "primary_report_identifier",
        False,
    )
    existing_mapping_df.unpersist()


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


def recursive_search_patient_web(
    spark: SparkSession,
    new_mapping_search_space: List[MappingEntry],
    existing_mapping_df: DataFrame,
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
        recursive_search_patient_web(
            spark,
            new_mapping_search_space,
            existing_mapping_df,
            known_mpis,
            known_epic_mrns,
            partial_search_results,
            case,
        )

    new_existing_mapping_finds_df = search_existing_on_patient_id(
        existing_mapping_df, new_mpi_revealed, new_mrn_revealed
    ).cache()
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
            recursive_search_patient_web(
                spark,
                new_mapping_search_space,
                existing_mapping_df,
                known_mpis,
                known_epic_mrns,
                partial_search_results,
                case,
            )

    new_existing_mapping_finds_df.unpersist()

    return partial_search_results
