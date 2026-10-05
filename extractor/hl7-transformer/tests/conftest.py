"""Shared pytest fixtures for the hl7-transformer unit tests.

These tests run a real local Spark + Delta session (no MinIO/Hive/Trino) so we can
exercise the derivative-table logic against an in-process metastore. Production config
is mirrored from the hl7-transformer chart's spark-defaults ConfigMap (the
"hl7-transformer.sparkDefaultsConf" helper in
helm/extractor/hl7-transformer/templates/_helpers.tpl), minus the S3A / remote
Hive settings.

Run from extractor/hl7-transformer. Spark/Delta versions are NOT pinned here — they come
from pyproject.toml (the single Python-side source of truth), so these tests track a
Spark upgrade automatically. Locally select a Spark-compatible interpreter + JVM: Spark
4.1.x runs on Python 3.10–3.13 and Java 17 or 21. Any supported pair works; the CI job
pins Python 3.10 to match the runtime image (spark:4.1.1-...-python3 ships Python 3.10),
so the tests exercise the interpreter that actually ships — matters for version-sensitive
behavior like concurrent.futures TimeoutError, which is a distinct class before 3.11:

    python3.10 -m venv .venv && .venv/bin/pip install -e '.[test]'
    JAVA_HOME=<java-17-or-21> .venv/bin/pytest tests/ -v
"""

import os

import pytest

# add_epic_views() does Trino DDL unless this is falsy; there is no Trino in unit tests.
os.environ.setdefault("TRINO_VIEW_ENABLED", "false")


@pytest.fixture(scope="session")
def spark(tmp_path_factory):
    from delta import configure_spark_with_delta_pip
    from pyspark.sql import SparkSession

    warehouse = tmp_path_factory.mktemp("warehouse")
    builder = (
        SparkSession.builder.appName("hl7scout-tests")
        .master("local[2]")
        .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
        .config(
            "spark.sql.catalog.spark_catalog",
            "org.apache.spark.sql.delta.catalog.DeltaCatalog",
        )
        # Mirrors production: Spark 4 enables ANSI mode by default, but the HL7
        # extraction relies on out-of-range array access and unparseable
        # timestamps yielding NULL.
        .config("spark.sql.ansi.enabled", "false")
        .config("spark.databricks.delta.schema.autoMerge.enabled", "true")
        .config("spark.databricks.delta.merge.repartitionBeforeWrite.enabled", "true")
        .config(
            "spark.databricks.delta.constraints.allowUnenforcedNotNull.enabled", "true"
        )
        .config("spark.sql.warehouse.dir", str(warehouse))
        .config("spark.sql.shuffle.partitions", "2")
        .config("spark.ui.enabled", "false")
    )
    session = configure_spark_with_delta_pip(builder).getOrCreate()
    session.sparkContext.setLogLevel("WARN")
    yield session
    session.stop()


# --- synthetic base `reports` table support -------------------------------------------
# A faithful-enough subset of the base reports schema: every column the derivative
# cascade (curated/latest/dx/mapping) actually reads. Missing per-authority id columns
# (bjh_mr, etc.) are tolerated by extract_from_anticipated_column, so we only include a
# representative few. Real HL7 parsing is covered by the Java tests/ingest suite.
# Note: seeded tables additionally carry the bookkeeping columns (updated,
# content_hash) added by merge_report_df_into_table.
import datetime as _dt  # noqa: E402

from pyspark.sql.types import (  # noqa: E402
    ArrayType,
    DateType,
    IntegerType,
    StringType,
    StructField,
    StructType,
    TimestampType,
)

_DIAGNOSIS_STRUCT = StructType(
    [
        StructField("diagnosis_code", StringType()),
        StructField("diagnosis_code_text", StringType()),
        StructField("diagnosis_code_coding_system", StringType()),
    ]
)

BASE_REPORTS_SCHEMA = StructType(
    [
        StructField("source_file", StringType()),
        StructField("message_control_id", StringType()),
        StructField("sending_facility", StringType()),
        StructField("version_id", StringType()),
        StructField("mpi", StringType()),
        StructField("epic_mrn", StringType()),
        StructField("empi_mr", StringType()),
        StructField("mbmc_mr", StringType()),
        StructField("orc_2_placer_order_number", StringType()),
        StructField("obr_2_placer_order_number", StringType()),
        StructField("orc_3_filler_order_number", StringType()),
        StructField("obr_3_filler_order_number", StringType()),
        StructField("birth_date", DateType()),
        StructField("message_dt", TimestampType()),
        StructField("requested_dt", TimestampType()),
        StructField("observation_dt", TimestampType()),
        StructField("diagnoses", ArrayType(_DIAGNOSIS_STRUCT)),
        StructField("diagnoses_consolidated", StringType()),
        StructField("year", IntegerType()),
    ]
)


@pytest.fixture
def report_row():
    """Returns a builder for a single synthetic base-reports row (dict keyed by column)."""

    def _build(
        source_file,
        *,
        version_id="2.7",
        mpi=None,
        epic_mrn="EPIC1",
        empi_mr=None,
        mbmc_mr=None,
        placer="PLC1",
        filler="ACC1",
        message_dt=None,
        diagnoses=(("J18.9", "Pneumonia", "ICD10"),),
        birth_date=None,
    ):
        message_dt = message_dt or _dt.datetime(2026, 1, 2, 3, 4, 5)
        return {
            "source_file": source_file,
            "message_control_id": source_file,
            "sending_facility": "TESTFAC",
            "version_id": version_id,
            "mpi": mpi,
            "epic_mrn": epic_mrn,
            "empi_mr": empi_mr,
            "mbmc_mr": mbmc_mr,
            "orc_2_placer_order_number": placer,
            "obr_2_placer_order_number": placer,
            "orc_3_filler_order_number": filler,
            "obr_3_filler_order_number": filler,
            "birth_date": birth_date or _dt.date(1980, 1, 1),
            "message_dt": message_dt,
            "requested_dt": message_dt,
            "observation_dt": message_dt,
            "diagnoses": [tuple(d) for d in diagnoses] if diagnoses else None,
            "diagnoses_consolidated": (
                "; ".join(d[1] for d in diagnoses) if diagnoses else None
            ),
            "year": message_dt.year,
        }

    return _build


@pytest.fixture
def report_df(spark):
    """Returns fn(rows) that builds a base-reports DataFrame (BASE_REPORTS_SCHEMA)
    from report_row dicts."""

    def _build(rows):
        ordered = [
            tuple(row[f.name] for f in BASE_REPORTS_SCHEMA.fields) for row in rows
        ]
        return spark.createDataFrame(ordered, BASE_REPORTS_SCHEMA)

    return _build


@pytest.fixture
def seed_reports(spark, report_df):
    """Returns fn(table_name, rows) that upserts the rows into the base reports Delta
    table through merge_report_df_into_table — exactly the path the base activity
    uses (bookkeeping columns, content-hash pre-filter, conditional merge), so it
    produces the same change data feed. Returns True iff a merge commit executed."""
    from hl7scout.hl7extractor.deltalake import merge_report_df_into_table

    def _seed(table_name, rows):
        return merge_report_df_into_table(spark, report_df(rows), table_name)

    return _seed
