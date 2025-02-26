import argparse
import dataclasses
import logging
import os
import sys
from collections.abc import Iterator
from dataclasses import asdict
from typing import Optional, Iterable

import s3fs
from delta import configure_spark_with_delta_pip
from delta.tables import DeltaTable
from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import ArrayType, StructType, StructField, StringType
from temporalio.exceptions import ApplicationError

from temporalpy.hl7extractor.hl7 import (
    MessageData,
    read_hl7_message,
    extract_data,
    parse_hl7_message,
    PatientIdentifier,
)

log = logging.getLogger(__name__)


s3filesystem = None

DATE_FORMAT = "yyyyMMdd"
DT_FORMAT = "yyyyMMddHHmmss"


def dataclass_to_spark_schema(dataclass) -> StructType:
    """Turn a dataclass into a spark struct of all nullable strings"""
    return StructType(
        [
            StructField(field.name, StringType(), True)
            for field in dataclasses.fields(dataclass)
        ]
    )


MESSAGE_SCHEMA = dataclass_to_spark_schema(MessageData)
JSON_ARRAY_SCHEMA = ArrayType(dataclass_to_spark_schema(PatientIdentifier))


def read_hl7_directory(
    directory: str, cache: Optional[set[str]] = None
) -> Iterator[MessageData]:
    """Read HL7 messages from directory."""
    cache = cache or set()
    if directory in cache:
        return

    for root, subdir, files in os.walk(directory):
        for file in files:
            path = os.path.join(root, file)
            if path in cache:
                continue
            if file.endswith(".hl7"):
                yield read_hl7_message(path)
                cache.add(path)

    cache.add(directory)


def read_hl7_s3(s3path: str, cache: Optional[set[str]] = None) -> Iterator[MessageData]:
    """Read HL7 messages from S3."""
    cache = cache or set()
    if s3path in cache:
        return

    global s3filesystem
    if s3filesystem is None:
        s3filesystem = s3fs.S3FileSystem()

    def read_hl7_file_from_s3(path: str) -> MessageData:
        log.info("Reading HL7 message from S3 file %s", path)
        with s3filesystem.open(path, "rb") as f:
            try:
                message = parse_hl7_message(f)
                log.debug("Successfully read HL7 message from %s", path)
                return extract_data(message, path)
            except Exception as e:
                raise ApplicationError(f"Error extracting {path}") from e

    if s3path.endswith(".hl7"):
        cache.add(s3path)
        yield read_hl7_file_from_s3(s3path)
    else:
        for p in s3filesystem.glob(s3path + "**.hl7"):
            if p in cache:
                continue
            cache.add(p)
            yield read_hl7_file_from_s3(p)


def read_hl7_input(hl7input: Iterable[str]) -> Iterator[MessageData]:
    """Read HL7 messages from input files or directories."""
    cache = set()
    for path in hl7input:
        if path in cache:
            continue
        if path.startswith("s3://"):
            yield from read_hl7_s3(path, cache=cache)
        elif os.path.isdir(path):
            yield from read_hl7_directory(path, cache=cache)
        else:
            yield read_hl7_message(path)
            cache.add(path)


def import_hl7_files_to_deltalake(
    delta_table: str, hl7_input: list[str], modality_map_csv_path: str
):
    """Extract data from HL7 messages and write to Delta Lake."""
    log.info(f"Reading HL7 messages from {len(hl7_input)} input files or directories")
    if not hl7_input:
        raise ApplicationError("No HL7 input files or directories provided")

    extra_packages = ["org.apache.hadoop:hadoop-aws:3.2.2"]
    log.debug("Creating Spark session")
    spark_builder = (
        SparkSession.builder.appName("IngestHL7ToDeltaLake")
        .config("spark.databricks.delta.schema.autoMerge.enabled", "true")
        .config("spark.databricks.delta.merge.repartitionBeforeWrite.enabled", "true")
        # TODO spark config
        .config("spark.hadoop.fs.s3a.access.key", "admin")
        .config("spark.hadoop.fs.s3a.secret.key", "password")
        .config("spark.hadoop.fs.s3a.endpoint", "http://minio.minio:9000")
        .config("spark.hadoop.fs.s3a.endpoint.region", "us-east-1")
        .config("spark.hadoop.fs.s3a.path.style.access", "true")
        .config(
            "spark.hadoop.fs.s3a.aws.credentials.provider",
            "org.apache.hadoop.fs.s3a.SimpleAWSCredentialsProvider",
        )
        .config("spark.driver.extraJavaOptions", "-Divy.cache.dir=/tmp -Divy.home=/tmp")
    )
    spark = configure_spark_with_delta_pip(
        spark_builder, extra_packages=extra_packages
    ).getOrCreate()

    log.debug("Reading HL7 messages")
    df = spark.createDataFrame(
        (
            asdict(message)
            for message in read_hl7_input(hl7_input)
            if message is not None
        ),
        schema=MESSAGE_SCHEMA,
    )

    if df.isEmpty():
        raise ApplicationError("No data extracted from HL7 messages")

    log.info(f"Extracted data from {df.count()} HL7 messages")

    # Read modality map
    log.info("Reading modality map from %s", modality_map_csv_path)
    modality_map = (
        spark.read.option("header", True)
        .csv(modality_map_csv_path)
        .select(
            F.col("Exam Code").alias("service_identifier"),
            F.col("Modality").alias("modality"),
        )
    )

    # Extract patient_id_json into distinct columns
    patient_id_df = (
        df.select("message_control_id", "patient_id_json")
        .withColumn(
            "patient_id",
            F.explode(F.from_json(F.col("patient_id_json"), JSON_ARRAY_SCHEMA)),
        )
        .withColumn(
            "patient_id_col_name",
            F.concat_ws(
                "_",
                F.lower(F.col("patient_id.assigning_authority")),
                F.lower(F.col("patient_id.identifier_type_code")),
            ),
        )
        .groupBy("message_control_id")
        .pivot("patient_id_col_name")
        # Assume they only have one patient id for each type
        .agg(F.first("patient_id.id_number"))
    )
    if log.isEnabledFor(logging.DEBUG):
        # TODO Is it ok to log the types of patient ids?
        #   We definitely aren't logging any specific values, just what kinds of ids exist.
        log.debug(
            "Extracted patient ids from HL7 messages. Cols: %s",
            ", ".join(c for c in patient_id_df.columns if c != "message_control_id"),
        )

    # Join data with modality map and exploded patient ids
    df = (
        df.join(modality_map, "service_identifier", "left")
        .select(
            "modality",
            "message_control_id",
            F.to_timestamp("message_dt", DT_FORMAT).alias("message_dt"),
            F.to_timestamp("birth_date", DATE_FORMAT).alias("birth_date"),
            "sex",
            "race",
            "zip_or_postal_code",
            "country",
            "ethnic_group",
            F.coalesce("orc_2_placer_order_number", "obr_2_placer_order_number").alias(
                "placer_order_number"
            ),
            F.coalesce("orc_3_filler_order_number", "obr_3_filler_order_number").alias(
                "filler_order_number"
            ),
            "service_identifier",
            "service_name",
            "service_coding_system",
            F.to_timestamp("requested_dt", DT_FORMAT).alias("requested_dt"),
            F.to_timestamp("observation_dt", DT_FORMAT).alias("observation_dt"),
            F.to_timestamp("observation_end_dt", DT_FORMAT).alias("observation_end_dt"),
            F.to_timestamp("results_report_status_change_dt", DT_FORMAT).alias(
                "results_report_status_change_dt"
            ),
            "diagnostic_service_id",
            "report_text",
            F.coalesce(
                "obx_11_observation_result_status", "obr_25_result_status"
            ).alias("report_status"),
            "diagnosis_code",
            "diagnosis_code_text",
            "diagnosis_code_coding_system",
            "study_instance_uid",
            "sending_facility",
            "version_id",
            "source_file",
            "patient_id_json",
        )
        .withColumn("year", F.year("message_dt"))
        .withColumn("updated", F.current_timestamp())
        .join(patient_id_df, "message_control_id", "left")
    )

    # Create table if it doesn't yet exist
    delta_table = delta_table.replace("s3://", "s3a://")

    dt = (
        DeltaTable.createIfNotExists(spark)
        .location(delta_table)
        .addColumns(df.schema)
        .partitionedBy("year")
        .execute()
    )

    log.info(f"Writing data to Delta Lake table {delta_table}")
    (
        dt.alias("s")
        .merge(
            df.alias("t"),
            "s.message_control_id = t.message_control_id AND s.year = t.year",
        )
        .whenMatchedUpdateAll()
        .whenNotMatchedInsertAll()
        .execute()
    )


def delete_delta_table(delta_table: str) -> None:
    """Delete Delta Lake table."""
    log.info(f"Deleting Delta Lake table {delta_table}")
    dt = DeltaTable(delta_table)
    dt.delete()
    dt.vacuum()


def main_cli(argv=None) -> int:
    """Main entry point for the CLI."""
    if argv is None:
        argv = sys.argv[1:]

    parser = argparse.ArgumentParser(
        description="Extract data from HL7 messages and write to Delta Lake",
    )
    parser.add_argument(
        "--delete",
        help="Delete Delta Lake table",
        action="store_true",
    )
    parser.add_argument(
        "--debug",
        help="Turn on debug logging",
        action="store_true",
    )
    parser.add_argument(
        "delta_table",
        help="Path to Delta Lake table",
    )
    parser.add_argument(
        "modality_map_csv_path",
        help="Path to modality map CSV file",
    )
    parser.add_argument(
        "hl7_input",
        help="HL7 input files or directories",
        nargs="+",
    )

    args = parser.parse_args(argv)
    if args.delete:
        delete_delta_table(args.delta_table)
        return 0

    if args.debug:
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        )
    else:
        logging.basicConfig(
            level=logging.INFO,
            format="%(message)s",
        )

    try:
        import_hl7_files_to_deltalake(
            args.delta_table, args.hl7_input, args.modality_map_csv_path
        )
        log.info("success")
        return 0
    except Exception as e:
        log.exception("Error extracting HL7 messages", exc_info=e)
        return 1


if __name__ == "__main__":
    sys.exit(main_cli())
