from dataclasses import dataclass
from typing import Callable, List, Optional
from temporalio import activity

from delta import DeltaTable
from pyspark.sql import SparkSession, DataFrame, Window
from pyspark.sql import functions as F


@dataclass
class DerivativeTable:
    name: str
    source_table: str
    process_source_data: Callable[[DataFrame, SparkSession, str], None]


def define_derivative_tables(report_table_name: str):
    return [
        DerivativeTable(
            name=f"{report_table_name}_latest",
            source_table=report_table_name,
            process_source_data=process_latest_table,
        ),
        DerivativeTable(
            name=f"{report_table_name}_dx",
            source_table=f"{report_table_name}_latest",
            process_source_data=process_dx_denormalize,
        ),
    ]


def perform_table_operations(spark: SparkSession, tables: List[DerivativeTable]):
    source_table = tables[0].source_table

    def streaming_function(batch_df, batch_id):
        for table in tables:
            table.process_source_data(batch_df, spark, table.name)

    full_table = (
        spark.readStream.format("delta")
        .option("readChangeFeed", "true")
        .table(f"default.{source_table}")
    )
    latest_table_operation = (
        full_table.writeStream.foreachBatch(streaming_function)
        .option("checkpointLocation", f"s3a://scratch/checkpoints/{source_table}")
        .trigger(availableNow=True)
        .start()
    )

    latest_table_operation.awaitTermination()


def process_latest_table(batch_df, spark, base_table_name):
    """
    Keeps only 1 report per accession number
    """

    table_name = f"default.{base_table_name}"
    deduped_df = dedupe_df_on_accession_number(batch_df)
    if deduped_df is None:
        return

    # Update existing latest table or create it if it does not yet exist
    if spark.catalog.tableExists(table_name):
        latest_table = DeltaTable.forName(spark, table_name)
        update_set = {
            col_name: F.col(f"s.{col_name}") for col_name in deduped_df.columns
        }

        latest_table.alias("t").merge(
            deduped_df.alias("s"),
            "t.obr_3_filler_order_number = s.obr_3_filler_order_number",
        ).whenMatchedUpdate(
            "s.message_dt > t.message_dt",
            update_set,  # Use exact columns as in source
        ).whenNotMatchedInsertAll().execute()  # If no existing row for accession number, insert it as-is
    else:
        create_table_from_df(deduped_df, base_table_name)


def process_dx_denormalize(batch_df, spark, base_table_name):
    """
    Uses latest table
    """
    deduped_df = dedupe_df_on_accession_number(batch_df)
    if deduped_df is None:
        return

    table_name = f"default.{base_table_name}"

    exploded_df = (
        deduped_df.withColumn("diagnosis", F.explode("diagnoses"))
        .withColumn("diagnosis_code", F.col("diagnosis.diagnosis_code"))
        .withColumn("diagnosis_code_text", F.col("diagnosis.diagnosis_code_text"))
        .withColumn(
            "diagnosis_code_coding_system", F.col("diagnosis_code_coding_system")
        )
        .drop("diagnosis", "diagnoses", "diagnoses_consolidated")
    )

    # For accession numbers we already have, delete previous diagnoses to replace them
    if spark.catalog.tableExists(table_name):
        accession_numbers = exploded_df.select("obr_3_filler_order_number").distinct()
        existing_table = DeltaTable.forName(spark, table_name)

        existing_table.alias("t").merge(
            accession_numbers.alias("s"),
            "t.obr_3_filler_order_number = s.obr_3_filler_order_number",
        ).whenMatchedDelete().execute()

    create_table_from_df(exploded_df, base_table_name)


def process_derivative_data(spark: SparkSession, report_table_name: str):
    derivative_tables = define_derivative_tables(report_table_name)
    activity.heartbeat()
    activity.logger.info(
        f"Processing derivative data tables: {[table.name for table in derivative_tables]}"
    )
    tables_without_dependencies = []
    tables_with_dependencies = []
    for table in derivative_tables:
        (
            tables_without_dependencies
            if table.source_table == report_table_name
            else tables_with_dependencies
        ).append(table)
    activity.heartbeat()
    activity.logger.info(
        f"Derivative tables without dependencies updated: {[table.name for table in tables_without_dependencies]}"
    )
    for table in tables_without_dependencies:
        # TODO: this is currently lazy just to make a PoC. This (and realistically the preceding part) should all just resolve the derivative tables as a DAG
        perform_table_operations(spark, [table])
        activity.heartbeat()
        activity.logger.info(f"Derivative table {table.name} updated")


def dedupe_df_on_accession_number(batch_df: DataFrame) -> Optional[DataFrame]:
    if batch_df.isEmpty():
        return None

    updates_insert_df = batch_df.filter(
        F.col("_change_type").isin(["insert", "update_postimage"])
    ).drop("_change_type", "_commit_version", "_commit_timestamp")

    if updates_insert_df.isEmpty():
        return None

    # First, make sure our new data only has the newest report per accession number
    dedupe_window = Window.partitionBy("obr_3_filler_order_number").orderBy(
        F.desc("message_dt")
    )
    # We have to create an explicit column instead of filtering by window function
    # even though it looks tempting to put it all in the filter
    return (
        updates_insert_df.withColumn("report_index", F.row_number().over(dedupe_window))
        .filter(F.col("report_index") == 1)
        .drop("report_index")
    )


def create_table_from_df(df: DataFrame, table_name: str):
    (
        df.write.format("delta")
        .option("delta.enableChangeDataFeed", "true")
        .mode("append")
        .saveAsTable(f"default.{table_name}")
    )
