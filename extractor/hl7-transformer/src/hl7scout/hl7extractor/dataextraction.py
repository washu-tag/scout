from dataclasses import dataclass, field
from typing import Callable, Optional, Dict
from temporalio import activity

from pyspark.sql import SparkSession, DataFrame, Window

from .curatedtable import curated_table
from .diagnosistable import diagnosis_table
from .latesttable import latest_table
from .derivativetable import DerivativeTable


def define_derivative_tables(report_table_name: str):
    return {
        table.table_name: table
        for table in [
            curated_table(report_table_name),
            latest_table(report_table_name),
            diagnosis_table(report_table_name),
        ]
    }


def perform_table_operations(
    spark: SparkSession, source_table: str, tables: Dict[str, DerivativeTable]
):
    def streaming_function(batch_df, batch_id):
        for name, table in tables.items():
            table.process_source_data(batch_df, spark, f"default.{name}")

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

    activity.heartbeat()
    activity.logger.info(f"Derivative tables {tables.keys()} updated")

    for child_name, child_table in tables.items():
        perform_table_operations(spark, child_name, child_table.children_tables)


def process_derivative_data(spark: SparkSession, report_table_name: str):
    derivative_tables = define_derivative_tables(report_table_name)
    activity.heartbeat()
    activity.logger.info(
        f"Processing derivative data tables: {[table.name for name, table in derivative_tables]}"
    )
    # noinspection PyTypeChecker
    root_table = DerivativeTable(
        source_table=None,
        table_name=report_table_name,
        process_source_data=None,
    )
    derivative_tables[report_table_name] = root_table
    for name, table in derivative_tables.items():
        if table.source_table is not None:
            derivative_tables[table.source_table].children_tables[name] = table
    perform_table_operations(spark, report_table_name, root_table.children_tables)
