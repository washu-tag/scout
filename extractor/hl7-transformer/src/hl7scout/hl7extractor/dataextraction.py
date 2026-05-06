import contextvars
import os
import threading

import trino
from pyspark.sql.streaming import StreamingQuery
from temporalio import activity

from pyspark.sql import SparkSession

from .curatedtable import curated_table
from .diagnosistable import diagnosis_table
from .latesttable import latest_table
from .mappingtable import mapping_table
from .derivativetable import DerivativeTable


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
        activity.logger.info(
            "Processing batch (%d) for derivative tables with %d rows",
            batch_id,
            cached_df.count(),
        )
        try:
            for name, table in tables.items():
                activity.logger.info(
                    "Processing batch (%d) on derivative table %s", batch_id, name
                )
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


def process_derivative_data(
    spark: SparkSession, report_table_name: str, create_mapping: bool = True
):
    derivative_tables = define_derivative_tables(report_table_name)
    activity.heartbeat()

    mapping_table_name = f"{report_table_name}_report_patient_mapping"
    if not create_mapping:
        # Drop the mapping table from the derivation so we don't pay the
        # mapping-derivation cost. The epic views below also get skipped
        # because they join against this table.
        derivative_tables = {
            name: table
            for name, table in derivative_tables.items()
            if name != mapping_table_name
        }

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

    if not create_mapping:
        return

    for child_table in derivative_tables.keys():
        if child_table != mapping_table_name:
            add_epic_views(spark, child_table, mapping_table_name)


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


def add_epic_views(spark: SparkSession, source_table: str, mapping_table_name: str):
    def view_sql(view_name):
        return f"""
            CREATE OR REPLACE VIEW {view_name} AS
            WITH patient_ids AS (
                SELECT
                    scout_patient_id,
                    MAX(epic_mrn) AS resolved_epic_mrn,
                    MAX(mpi) AS resolved_mpi
                FROM {mapping_table_name}
                WHERE consistent = true
                GROUP BY scout_patient_id
            )
            SELECT
                r.*,
                m.scout_patient_id,
                p.resolved_epic_mrn,
                p.resolved_mpi
            FROM {source_table} r
            JOIN {mapping_table_name} m
                ON r.primary_report_identifier = m.primary_report_identifier
            JOIN patient_ids p
                ON m.scout_patient_id = p.scout_patient_id
            WHERE m.consistent = true
        """

    spark.sql(view_sql(f"{source_table}_spark_epic_view"))

    # Skip the Trino-side view creation when TRINO_VIEW_ENABLED is set to
    # something falsy. Defaults to enabled. Used by CI environments that
    # don't stand up trino-rw to keep the deploy footprint smaller.
    if os.environ.get("TRINO_VIEW_ENABLED", "true").strip().lower() != "true":
        return

    def trino_connection():
        return trino.dbapi.connect(
            host=os.environ["TRINO_HOST"],
            port=int(os.environ["TRINO_PORT"]),
            user=os.environ["TRINO_USER"],
            catalog=os.environ["TRINO_CATALOG"],
            schema="default",
            http_scheme=os.environ["TRINO_SCHEME"],
        )

    with trino_connection() as conn:
        cur = conn.cursor()
        cur.execute(view_sql(f"{source_table}_epic_view"))
        cur.fetchall()  # Trino DB-API requires fetching to actually execute
