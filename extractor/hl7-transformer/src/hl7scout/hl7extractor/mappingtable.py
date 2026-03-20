from typing import Optional

from delta import DeltaTable
from pyspark.sql.types import StructType, StructField, StringType, BooleanType
from temporalio import activity

from .derivativetable import DerivativeTable
from .sparkutils import (
    filter_df_for_update_inserts,
    merge_df_into_dt_on_column,
    empty_string_coalesce,
    create_table_from_df,
    extract_from_anticipated_column,
)

from pyspark.sql import functions as F, Column, DataFrame, Window


def create_exact_match_condition(df1: DataFrame, df2: DataFrame) -> Column:
    return (df1["mpi"].eqNullSafe(df2["mpi"])) & (
        df1["epic_mrn"].eqNullSafe(df2["epic_mrn"])
    )


def mapping_table(base_report_table_name: str) -> DerivativeTable:
    source_table = f"{base_report_table_name}_curated"

    def process_table(batch_df, spark, table_name):
        extract_mapping(batch_df, spark, table_name, source_table)

    return DerivativeTable(
        source_table=source_table,
        table_name="report_patient_mapping",
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
        existing_mapping_df = spark.createDataFrame(
            [],
            StructType(
                [
                    StructField("scout_patient_id", StringType(), True),
                    StructField("primary_report_identifier", StringType(), True),
                    StructField("mpi", StringType(), True),
                    StructField("epic_mrn", StringType(), True),
                    StructField("consistent", BooleanType(), True),
                ]
            ),
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

    unique_ids_incoming_reports = remaining_reports_ranked.filter(
        F.col("_rank") == 1
    ).drop("_rank")
    duplicate_ids_incoming_reports = remaining_reports_ranked.filter(
        F.col("_rank") > 1
    ).drop("_rank")

    deferred_reports_df = exact_matches_df.unionByName(
        duplicate_ids_incoming_reports
    ).dropDuplicates()

    remaining_reports_ranked.unpersist()

    activity.logger.info("Stage 1 completed on mapping table derivation")
    activity.heartbeat("Mapping table stage 1")

    # Stage 1 complete, begin stage 2
    remaining_reports_df = unique_ids_incoming_reports

    partial_match_condition = (
        remaining_reports_df["mpi"] == existing_mapping_df["mpi"]
    ) | (remaining_reports_df["epic_mrn"] == existing_mapping_df["epic_mrn"])
    partial_existing_mapping_match_df = remaining_reports_df.join(
        existing_mapping_df, on=partial_match_condition, how="left_semi"
    )
    no_existing_mapping_match_df = remaining_reports_df.join(
        existing_mapping_df, on=partial_match_condition, how="left_anti"
    )

    mpi_counts = (
        no_existing_mapping_match_df.filter(F.col("mpi").isNotNull())
        .groupBy("mpi")
        .agg(F.count("*").alias("mpi_count"))
    )
    epic_mrn_counts = (
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

    fully_disjoint_reports_df = filter_no_existing_mapping_df(
        (F.col("mpi_count") <= 1) & (F.col("epic_mrn_count") <= 1)
    )
    incoming_reports_with_links_df = filter_no_existing_mapping_df(
        (F.col("mpi_count") > 1) | (F.col("epic_mrn_count") > 1)
    )

    if not (fully_disjoint_reports_df.isEmpty()):
        fully_disjoint_reports_df = fully_disjoint_reports_df.select(
            F.expr("uuid()").alias("scout_patient_id"),
            "primary_report_identifier",
            "mpi",
            "epic_mrn",
            F.lit(True).alias("consistent"),
        )

        dt = (
            DeltaTable.createIfNotExists(spark)
            .tableName(f"default.{table_name}")
            .addColumns(fully_disjoint_reports_df.schema)
            .execute()
        )

        merge_df_into_dt_on_column(
            dt, fully_disjoint_reports_df, "primary_report_identifier"
        )
        existing_mapping_df = spark.read.table(table_name)

    # Stage 2 complete, begin stage 3

    activity.logger.info("Stage 2 completed on mapping table derivation")
    activity.heartbeat("Mapping table stage 2")

    remaining_reports_df = incoming_reports_with_links_df.unionByName(
        partial_existing_mapping_match_df
    ).dropDuplicates()
    activity.logger.warn(
        "Stages 3 and 4 have not yet been implemented, %d reports will be skipped",
        remaining_reports_df.count(),
    )

    # TODO: impl stages 3 & 4

    if not (deferred_reports_df.isEmpty()):
        dupes_to_add_df = deferred_reports_df.select(
            "primary_report_identifier"
        ).join(  # take only the report ID from the incoming data
            existing_mapping_df.drop(
                "primary_report_identifier"
            ),  # guaranteed a match, take everything from the match other than the report ID
            on=create_exact_match_condition(deferred_reports_df, existing_mapping_df),
            how="inner",
        )
        merge_df_into_dt_on_column(
            DeltaTable.forName(spark, table_name),
            dupes_to_add_df,
            "primary_report_identifier",
        )

    activity.logger.info("Mapping table derivation complete")
    activity.heartbeat("Mapping table complete")

    spark.sql(
        f"""
        CREATE OR REPLACE VIEW {source_table}_epic_view AS
        SELECT 
            r.*,
            m.scout_patient_id,
            m.epic_mrn AS resolved_epic_mrn,
            m.mpi AS resolved_mpi
        FROM {source_table} r
        JOIN report_patient_mapping m
          ON r.primary_report_identifier = m.primary_report_identifier
        WHERE m.consistent = true
    """
    )
