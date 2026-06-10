"""
Seed the synthetic delta.default.test_reports table the data-authorization
test runner asserts against. Runs as a one-shot Job in CI's smoke-test
job after the analytics playbook deploys; uses the hl7-transformer image
as its vehicle (PySpark + Delta + Hive metastore client + s3a + the
right jars in $SPARK_HOME/jars).

The schema mirrors the production reports table for the columns the
rego policy filters and masks against:
  - sending_facility (varchar)     -- row filter dimension #1
  - modality (varchar)             -- row filter dimension #2
  - patient_name (varchar)         -- PHI masked as '[REDACTED]'
  - full_patient_name (struct)     -- PHI masked as NULL (non-varchar)
  - zip_or_postal_code (varchar)   -- PHI masked as '[REDACTED]'

Three ABCHOSP* facilities x two modalities = six combinations, plus one row under
"HOME CARE SERVICES" — a facility whose name has spaces, which exercises the
space-tolerant filter value pattern in policy/trino/main.rego. Row counts chosen
so the assertions are unambiguous:
  ABCHOSP1: 3 rows  (2 CT, 1 MR)
  ABCHOSP2: 2 rows  (1 CT, 1 MR)
  ABCHOSP3: 1 row   (0 CT, 1 MR)
  HOME CARE SERVICES: 1 row (1 CT)
  Total: 7 rows

All config is read from env vars set by the Job spec — no spark-defaults
ConfigMap dependency, since the extractor role isn't deployed in
smoke-test and we don't want to fork that pattern for one table.
"""

import os
import sys
import time

from py4j.protocol import Py4JJavaError
from pyspark.sql import SparkSession
from pyspark.sql.types import (
    StringType,
    StructField,
    StructType,
)


def _env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        sys.stderr.write(f"missing required env var: {name}\n")
        sys.exit(1)
    return val


def wait_for_object_store(spark, probe_path, attempts=36, delay_seconds=5):
    """Block until MinIO accepts an authenticated request against the lake
    bucket. Right after the analytics stack deploys, MinIO can briefly return
    403 for the lake-writer credentials before its IAM subsystem is live,
    which fails the saveAsTable below (observed flaking the data-authorization
    smoke test). Probe with the same Hadoop s3a FileSystem Spark uses for the
    write: a missing path returns False (fine); only an auth/connection failure
    raises -- so success here means the write will authenticate too. Bounded
    well under the Job's wait budget so a genuine outage fails with this clear
    message instead of an opaque mid-write 403."""
    hadoop_conf = spark._jsc.hadoopConfiguration()
    path = spark._jvm.org.apache.hadoop.fs.Path(probe_path)
    fs = path.getFileSystem(hadoop_conf)
    last_err = None
    for attempt in range(1, attempts + 1):
        try:
            fs.exists(path)
            if attempt > 1:
                print(f"MinIO object store ready after {attempt} attempts")
            return
        except Py4JJavaError as err:
            last_err = err
            sys.stderr.write(
                f"waiting for MinIO object store ({attempt}/{attempts})...\n"
            )
            time.sleep(delay_seconds)
    raise RuntimeError(
        f"MinIO object store not ready after {attempts * delay_seconds}s: {last_err}"
    )


def main() -> None:
    hive_uri = _env("HIVE_METASTORE_URIS")
    s3_endpoint = _env("S3_ENDPOINT")
    s3_key = _env("AWS_ACCESS_KEY_ID")
    s3_secret = _env("AWS_SECRET_ACCESS_KEY")
    warehouse = _env("SPARK_SQL_WAREHOUSE_DIR")

    s3_region = os.environ.get("AWS_REGION", "us-east-1")

    spark = (
        SparkSession.builder.appName("seed-test-data")
        .config("spark.hadoop.fs.s3a.endpoint", s3_endpoint)
        .config("spark.hadoop.fs.s3a.endpoint.region", s3_region)
        .config("spark.hadoop.fs.s3a.access.key", s3_key)
        .config("spark.hadoop.fs.s3a.secret.key", s3_secret)
        .config("spark.hadoop.fs.s3a.path.style.access", "true")
        .config(
            "spark.hadoop.fs.s3a.aws.credentials.provider",
            "org.apache.hadoop.fs.s3a.SimpleAWSCredentialsProvider",
        )
        .config("spark.hadoop.hive.metastore.uris", hive_uri)
        .config("spark.sql.warehouse.dir", warehouse)
        .config(
            "spark.sql.extensions",
            "io.delta.sql.DeltaSparkSessionExtension",
        )
        .config(
            "spark.sql.catalog.spark_catalog",
            "org.apache.spark.sql.delta.catalog.DeltaCatalog",
        )
        # Single shuffle partition is plenty for 6 rows; default of 200
        # would create 200 empty Parquet part-files in the Delta table.
        .config("spark.sql.shuffle.partitions", "1")
        # CI's k3s has tight memory; trim Spark's defaults so the Job
        # fits inside the Pod's 1.5Gi limit. Local mode uses driver
        # memory for both driver and executor since they're the same
        # JVM.
        .config("spark.driver.memory", "512m")
        .enableHiveSupport()
        .getOrCreate()
    )

    # Gate the writes on MinIO being ready to authenticate the lake-writer
    # credentials (see wait_for_object_store). Without it the first saveAsTable
    # can hit a transient 403 in the post-deploy IAM-load window.
    wait_for_object_store(spark, warehouse)

    schema = StructType(
        [
            StructField("sending_facility", StringType(), nullable=False),
            StructField("modality", StringType(), nullable=False),
            StructField("patient_name", StringType(), nullable=False),
            StructField(
                "full_patient_name",
                StructType(
                    [
                        StructField("given", StringType(), nullable=True),
                        StructField("family", StringType(), nullable=True),
                    ]
                ),
                nullable=True,
            ),
            StructField("zip_or_postal_code", StringType(), nullable=True),
        ]
    )

    rows = [
        ("ABCHOSP1", "CT", "Alice Anderson", ("Alice", "Anderson"), "63110"),
        ("ABCHOSP1", "CT", "Bob Brown", ("Bob", "Brown"), "63111"),
        ("ABCHOSP1", "MR", "Carol Chen", ("Carol", "Chen"), "63112"),
        ("ABCHOSP2", "CT", "Dave Davis", ("Dave", "Davis"), "63113"),
        ("ABCHOSP2", "MR", "Eve Edwards", ("Eve", "Edwards"), "63114"),
        ("ABCHOSP3", "MR", "Frank Foster", ("Frank", "Foster"), "63115"),
        # Facility name with spaces — exercises the space-tolerant filter value
        # pattern (policy/trino/main.rego). "Zoe" sorts last so it never displaces
        # Alice as the PHI-mask scenarios' ORDER BY patient_name LIMIT 1 row.
        ("HOME CARE SERVICES", "CT", "Zoe Zimmerman", ("Zoe", "Zimmerman"), "63116"),
    ]

    df = spark.createDataFrame(rows, schema)
    # `overwrite` so re-running the Job (CI retries) leaves a known
    # state; the rego policy doesn't read from this table, only Trino
    # via the row filter / mask plan, so atomic Delta overwrite is fine.
    df.write.format("delta").mode("overwrite").saveAsTable("default.test_reports")
    count = spark.sql("SELECT COUNT(*) AS c FROM default.test_reports").collect()[0][
        "c"
    ]
    print(f"seeded delta.default.test_reports: {count} rows")

    # reports_report_patient_mapping is a baseline hidden table in
    # policy/trino/main.rego (denied for direct SELECT, bypassable via the
    # bypass_hidden_tables attribute). The hidden-table scenarios assert a
    # *permission* denial, so the table must actually exist — otherwise Trino
    # raises TABLE_NOT_FOUND during analysis before the OPA SelectFromColumns
    # check ever runs, and the test would pass for the wrong reason. Minimal
    # schema: it only needs to exist and be selectable.
    mapping_schema = StructType(
        [
            StructField("scout_patient_id", StringType(), nullable=False),
            StructField("epic_mrn", StringType(), nullable=True),
        ]
    )
    mapping_rows = [
        ("scout-0001", "MRN0001"),
        ("scout-0002", "MRN0002"),
    ]
    mapping_df = spark.createDataFrame(mapping_rows, mapping_schema)
    mapping_df.write.format("delta").mode("overwrite").saveAsTable(
        "default.reports_report_patient_mapping"
    )
    mapping_count = spark.sql(
        "SELECT COUNT(*) AS c FROM default.reports_report_patient_mapping"
    ).collect()[0]["c"]
    print(f"seeded delta.default.reports_report_patient_mapping: {mapping_count} rows")
    spark.stop()


if __name__ == "__main__":
    main()
