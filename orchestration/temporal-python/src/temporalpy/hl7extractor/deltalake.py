import os

from delta import configure_spark_with_delta_pip
from delta.tables import DeltaTable
from pyspark.sql import Column, SparkSession
from pyspark.sql import functions as F
from temporalio import activity
from temporalio.exceptions import ApplicationError

DATE_FORMAT = "yyyyMMdd"
DT_FORMAT = "yyyyMMddHHmmss"

EMPTY_FILTER = " and ".join(
    f"{field} is not null and {field} != ''"
    for field in ("id_number", "assigning_authority", "identifier_type_code")
)


def segment_field(
    segment: str,
    field: int,
    component: int = 1,
    segment_column: Column | str = "segments",
) -> Column:
    if segment == "MSH":
        segment_val = F.split("message", "\\|")
    else:
        segment_val = (
            F.filter(segment_column, lambda s: s["id"] == segment)
            .getItem(0)
            .getField("fields")
        )
    return F.split(segment_val.getField(field - 1), "\\^").getItem(component - 1)


def parse_timestamp_col(col: Column | str) -> Column:
    """Convert a timestamp column to a datetime column.
    Some of our timestamps are missing seconds, so we need to add them."""
    return F.to_timestamp(
        F.when(F.length(col) == 12, F.concat(col, F.lit("00"))).otherwise(col),
        DT_FORMAT,
    )


def import_hl7_files_to_deltalake(
    delta_table: str, hl7_manifest_file_path: str, modality_map_csv_path: str
) -> int:
    """Extract data from HL7 messages and write to Delta Lake."""

    # TODO This should be moved to a configuration file
    s3a_endpoint = os.environ.get("AWS_ENDPOINT_URL")
    s3a_access_key = os.environ.get("AWS_ACCESS_KEY_ID")
    s3a_secret_key = os.environ.get("AWS_SECRET_ACCESS_KEY")
    s3a_region = os.environ.get("AWS_REGION", "us-east-1")
    spark_executor_memory = os.environ.get("SPARK_EXECUTOR_MEMORY")

    if not s3a_endpoint or not s3a_access_key or not s3a_secret_key:
        raise ApplicationError("S3 endpoint, access key, and secret key required")

    try:
        activity.logger.info("Creating Spark session")
        extra_packages = ["org.apache.hadoop:hadoop-aws:3.2.2"]
        spark_builder = (
            SparkSession.builder.appName("IngestHL7ToDeltaLake")
            # .config("spark.jars", "/opt/spark/jars/*.jar")
            .config("spark.jars", "/opt/spark/jars/smolder_2.12-0.1.0-SNAPSHOT.jar")
            .config("spark.databricks.delta.schema.autoMerge.enabled", "true")
            .config(
                "spark.databricks.delta.merge.repartitionBeforeWrite.enabled", "true"
            )
            .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
            .config(
                "spark.sql.catalog.spark_catalog",
                "org.apache.spark.sql.delta.catalog.DeltaCatalog",
            )
            .config("spark.hadoop.fs.s3a.access.key", s3a_access_key)
            .config("spark.hadoop.fs.s3a.secret.key", s3a_secret_key)
            .config("spark.hadoop.fs.s3a.endpoint", s3a_endpoint)
            .config("spark.hadoop.fs.s3a.endpoint.region", s3a_region)
            .config("spark.hadoop.fs.s3a.path.style.access", "true")
            .config(
                "spark.hadoop.fs.s3a.aws.credentials.provider",
                "org.apache.hadoop.fs.s3a.SimpleAWSCredentialsProvider",
            )
            .config(
                "spark.driver.extraJavaOptions", "-Divy.cache.dir=/tmp -Divy.home=/tmp"
            )
        )

        if spark_executor_memory:
            spark_builder.config("spark.executor.memory", spark_executor_memory)
            spark_builder.config("spark.driver.memory", spark_executor_memory)
        spark = configure_spark_with_delta_pip(
            spark_builder, extra_packages=extra_packages
        ).getOrCreate()

        activity.logger.info("Reading HL7 manifest file %s", hl7_manifest_file_path)
        hl7_manifest_file_path = hl7_manifest_file_path.replace("s3://", "s3a://")
        file_path_file_df = spark.read.text(hl7_manifest_file_path)

        # I wish I could just have spark read these directly without collecting first but I can't figure out how
        hl7_file_paths_from_spark = [
            row.value.replace("s3://", "s3a://") for row in file_path_file_df.collect()
        ]
        if not hl7_file_paths_from_spark:
            raise ApplicationError("No HL7 files found in HL7 file path files")

        activity.logger.info("Reading %d HL7 messages", len(hl7_file_paths_from_spark))
        df = (
            spark.read.format("hl7")
            .load(hl7_file_paths_from_spark)
            .withColumn("source_file", F.input_file_name())
        )

        if df.isEmpty():
            raise ApplicationError("No data extracted from HL7 messages")

        num_hl7 = df.count()
        activity.logger.info("Extracted data from %d HL7 messages", num_hl7)

        # Read modality map
        modality_map_csv_path = modality_map_csv_path.replace("s3://", "s3a://")
        activity.logger.info("Reading modality map from %s", modality_map_csv_path)
        modality_map = (
            spark.read.option("header", True)
            .csv(modality_map_csv_path)
            .select(
                F.col("Exam Code").alias("service_identifier"),
                F.col("Modality").alias("modality"),
            )
        )

        activity.logger.info("Creating report df")
        report_df = (
            df.select(
                "source_file",
                # Get all OBX segments and explode into separate rows, keeping the index as "pos" column
                F.posexplode(F.filter("segments", lambda s: s["id"] == "OBX")),
            )
            .select(
                "source_file",
                # "pos" holds the index of the OBX segment
                "pos",
                # "col" holds the OBX segment data
                F.when(
                    # TX data type has a ~ delimiter, replace with newline
                    F.col("col").getField("fields").getItem(1) == "TX",
                    F.regexp_replace(
                        F.col("col").getField("fields").getItem(4), "~", "\n"
                    ),
                )
                # Other data types are a single line as the value
                .otherwise(F.col("col").getField("fields").getItem(4)).alias("obx-5"),
                F.col("col").getField("fields").getItem(10).alias("obx-11"),
            )
            .groupby("source_file")
            .agg(
                # Join all lines of report text into one string
                F.concat_ws("\n", F.collect_list("obx-5")).alias("report_text"),
                # Assume report statuses are the same, pick first
                F.first("obx-11").alias("report_status"),
            )
        )

        activity.logger.info("Extracting patient id columns")
        exploded_patient_id_df = df.select(
            "source_file",
            F.explode(
                F.split(
                    F.filter("segments", lambda s: s["id"] == "PID")
                    .getItem(0)
                    .getField("fields")
                    .getItem(2),
                    "~",
                )
            ).alias("pid"),
        ).select(
            "source_file",
            F.split("pid", "\\^").getItem(0).alias("id_number"),
            F.split("pid", "\\^").getItem(3).alias("assigning_authority"),
            F.split("pid", "\\^").getItem(4).alias("identifier_type_code"),
        )

        patient_id_df = (
            # Filter out patient ids with missing fields
            exploded_patient_id_df.filter(EMPTY_FILTER)
            .select(
                "source_file",
                "id_number",
                F.concat_ws(
                    "_",
                    F.lower("assigning_authority"),
                    F.lower("identifier_type_code"),
                ).alias("patient_id_col_name"),
            )
            .groupBy("source_file")
            # Turn the patient_id_col_name values into columns
            .pivot("patient_id_col_name")
            # Assume they only have one patient id for each type
            .agg(F.first("id_number"))
        )
        # Note that we do not filter out any patient ids here, so they are all available in the JSON
        patient_id_json_df = exploded_patient_id_df.groupBy("source_file").agg(
            F.to_json(
                F.collect_list(
                    F.struct("id_number", "assigning_authority", "identifier_type_code")
                )
            ).alias("patient_id_json")
        )

        activity.logger.info("Joining data into report df")
        df = (
            df.select(
                segment_field("MSH", 10).alias("message_control_id"),
                segment_field("MSH", 4).alias("sending_facility"),
                segment_field("MSH", 12).alias("version_id"),
                parse_timestamp_col(segment_field("MSH", 7)).alias("message_dt"),
                F.to_date(
                    F.substring(segment_field("PID", 7), 1, 8), DATE_FORMAT
                ).alias("birth_date"),
                segment_field("PID", 8).alias("sex"),
                segment_field("PID", 10).alias("race"),
                segment_field("PID", 11, 5).alias("zip_or_postal_code"),
                segment_field("PID", 11, 6).alias("country"),
                segment_field("PID", 22).alias("ethnic_group"),
                segment_field("ORC", 2).alias("orc_2_placer_order_number"),
                segment_field("OBR", 2).alias("obr_2_placer_order_number"),
                segment_field("ORC", 3).alias("orc_3_filler_order_number"),
                segment_field("OBR", 3).alias("obr_3_filler_order_number"),
                segment_field("OBR", 4, 1).alias("service_identifier"),
                segment_field("OBR", 4, 2).alias("service_name"),
                segment_field("OBR", 4, 3).alias("service_coding_system"),
                parse_timestamp_col(segment_field("OBR", 6)).alias("requested_dt"),
                parse_timestamp_col(segment_field("OBR", 7)).alias("observation_dt"),
                parse_timestamp_col(segment_field("OBR", 8)).alias(
                    "observation_end_dt"
                ),
                parse_timestamp_col(segment_field("OBR", 22)).alias(
                    "results_report_status_change_dt"
                ),
                segment_field("OBR", 24).alias("diagnostic_service_id"),
                segment_field("DG1", 3, 1).alias("diagnosis_code"),
                segment_field("DG1", 3, 2).alias("diagnosis_code_text"),
                segment_field("DG1", 3, 3).alias("diagnosis_code_coding_system"),
                segment_field("ZDS", 1).alias("study_instance_uid"),
                "source_file",
            )
            .join(report_df, "source_file", "left")
            .join(patient_id_df, "source_file", "left")
            .join(patient_id_json_df, "source_file", "left")
            .join(modality_map, "service_identifier", "left")
            .withColumn("year", F.year("message_dt"))
            .withColumn("updated", F.current_timestamp())
        )

        # Create table if it doesn't yet exist
        delta_table = delta_table.replace("s3://", "s3a://")

        activity.logger.info(
            "Creating Delta Lake table %s if it does not exist", delta_table
        )
        dt = (
            DeltaTable.createIfNotExists(spark)
            .location(delta_table)
            .addColumns(df.schema)
            .partitionedBy("year")
            .execute()
        )

        activity.logger.info("Writing data to Delta Lake table %s", delta_table)
        (
            dt.alias("s")
            .merge(
                df.alias("t"),
                "s.source_file = t.source_file AND s.year = t.year",
            )
            .whenMatchedUpdateAll()
            .whenNotMatchedInsertAll()
            .execute()
        )
    except:
        activity.logger.exception("Error ingesting HL7 files to Delta Lake")
        raise
    finally:
        if spark is not None:
            activity.logger.info("Stopping spark")
            spark.stop()

    activity.logger.info("Finished")
    return num_hl7
