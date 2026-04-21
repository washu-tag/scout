import contextvars
import threading

from pyspark.sql.streaming import StreamingQuery
from temporalio import activity

from pyspark.sql import SparkSession

from .curatedtable import curated_table
from .diagnosistable import diagnosis_table
from .latesttable import latest_table
from .mappingtable import mapping_table
from .derivativetable import DerivativeTable
from .sparkutils import add_epic_view


def define_derivative_tables(report_table_name: str) -> dict[str, DerivativeTable]:
    return {
        table.table_name: table
        for table in [
            curated_table(report_table_name),
            latest_table(report_table_name),
            mapping_table(report_table_name),
            diagnosis_table(report_table_name),
        ]
    }


def perform_table_operations(
    spark: SparkSession, source_table: str, tables: dict[str, DerivativeTable]
):
    def streaming_function(batch_df, batch_id):
        cached_df = batch_df.cache()
        activity.logger.info("Processing batch (%d) for derivative tables with %d rows", batch_id, cached_df.count())
        try:
            for name, table in tables.items():
                activity.logger.info("Processing batch (%d) on derivative table %s", batch_id, name)
                table.process_source_data(cached_df, spark, f"default.{name}")
        finally:
            cached_df.unpersist()

    warehouse_dir = spark.conf.get("spark.sql.warehouse.dir")

    full_table = (
        spark.readStream.format("delta")
        .option("readChangeFeed", "true")
        .table(f"default.{source_table}")
    )

    perform_derivative_operation_with_heartbeat(
        full_table.writeStream.foreachBatch(streaming_function)
        .option("checkpointLocation", f"{warehouse_dir}/checkpoints/{source_table}")
        .trigger(availableNow=True)
        .start()
    )

    activity.heartbeat()
    activity.logger.info(f"Derivative tables {tables.keys()} updated")

    for child_name, child_table in tables.items():
        perform_table_operations(spark, child_name, child_table.children_tables)


def process_derivative_data(spark: SparkSession, report_table_name: str):
    derivative_tables = define_derivative_tables(report_table_name)
    activity.heartbeat()
    activity.logger.info(
        f"Processing derivative data tables: {[table.table_name for table in derivative_tables.values()]}"
    )

    root_table_children = {}
    for name, table in derivative_tables.items():
        if table.source_table == report_table_name:
            root_table_children[name] = table
        else:
            derivative_tables[table.source_table].children_tables[name] = table
    perform_table_operations(spark, report_table_name, root_table_children)

    mapping_table_name = f"{report_table_name}_report_patient_mapping"
    for child_table in derivative_tables.keys():
        if child_table != mapping_table_name:
            add_epic_view(spark, child_table, mapping_table_name)


def perform_derivative_operation_with_heartbeat(table_operation: StreamingQuery):
    ctx = contextvars.copy_context()
    stop_event = threading.Event()

    def heartbeat_loop():
        while not stop_event.wait(timeout=10):
            ctx.run(activity.heartbeat)  # need right context for activity heartbeat

    t = threading.Thread(target=heartbeat_loop, daemon=True)
    t.start()
    try:
        table_operation.awaitTermination()
    finally:
        stop_event.set()
        t.join()
