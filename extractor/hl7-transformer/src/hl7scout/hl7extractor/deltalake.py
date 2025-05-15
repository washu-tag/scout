# I don't know why we have to import this specific exception type, since it is
# literally just an alias for the builtin TimeoutError.
# But if we try to catch the builtin TimeoutError, it doesn't work.
from concurrent.futures._base import TimeoutError
from collections import defaultdict
from pathlib import Path

from delta.tables import DeltaTable
from py4j.protocol import Py4JError
from pyspark.sql import Column, SparkSession
from pyspark.sql import functions as F
from s3fs import S3FileSystem
from temporalio import activity
from temporalio.exceptions import ApplicationError

from hl7scout.db import write_errors, write_successes

import os
import shutil
import tempfile
import zipfile

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


def parse_s3_zip_paths(hl7_file_paths: list[str]) -> dict[str, list[str]]:
    """Parse S3 zip paths into a dictionary mapping zip files to their contents."""
    zip_map = defaultdict(list)
    for path in hl7_file_paths:
        zip_path, hl7_inside_zip = path.split(".zip/")
        zip_map[f"{zip_path}.zip"].append(hl7_inside_zip)
    return zip_map


def download_and_extract_zips(zip_map, local_dir):
    """Download and extract zip files from S3 to a local directory."""
    fs = S3FileSystem()
    extracted_files = []
    for s3_zip_path, hl7_files in zip_map.items():
        local_zip = os.path.join(local_dir, os.path.basename(s3_zip_path))
        fs.download(s3_zip_path, local_zip)
        with zipfile.ZipFile(local_zip, "r") as z:
            for hl7_file in hl7_files:
                out_path = os.path.join(local_dir, hl7_file)
                z.extract(hl7_file, local_dir)
                extracted_files.append((out_path, f"{s3_zip_path}/{hl7_file}"))
    return extracted_files


def import_hl7_files_to_deltalake(
    hl7_manifest_file_path: str,
    modality_map_csv_path: str,
    report_table_name: str,
    health_file: Path,
) -> int:
    """Extract data from HL7 messages and write to Delta Lake."""
    spark = None
    temp_dir = None
    try:
        activity.logger.info("Creating Spark session")
        spark = (
            SparkSession.builder.appName("IngestHL7ToDeltaLake")
            .enableHiveSupport()
            .getOrCreate()
        )

        activity.heartbeat()
        activity.logger.info("Reading HL7 manifest file %s", hl7_manifest_file_path)
        hl7_manifest_file_path = hl7_manifest_file_path.replace("s3://", "s3a://")
        file_path_file_df = spark.read.text(hl7_manifest_file_path)

        zipped_hl7_file_paths_from_spark = [
            row.value.replace("s3://", "s3a://") for row in file_path_file_df.collect()
        ]
        if not zipped_hl7_file_paths_from_spark:
            raise ApplicationError("No HL7 files found in HL7 file path files")

        activity.heartbeat()
        activity.logger.info("Downloading and extracting HL7 files")

        temp_dir = tempfile.mkdtemp()
        zip_map = parse_s3_zip_paths(zipped_hl7_file_paths_from_spark)
        temp_to_s3 = download_and_extract_zips(zip_map, temp_dir)
        temp_hl7_files = [temp_path for temp_path, _ in temp_to_s3]

        activity.heartbeat()
        activity.logger.info("Reading %d HL7 messages", len(temp_hl7_files))

        # Create a DataFrame with the temp file paths and their corresponding S3 paths
        temp_to_s3_df = spark.createDataFrame(temp_to_s3, ["temp_path", "source_file"])
        temp_to_s3_df = temp_to_s3_df.withColumn(
            "source_file",
            F.regexp_replace(F.col("source_file"), "^s3a://", "s3://"),
        )

        # Spark appends "file://" to the path, so we need to add it here for the join to work
        temp_to_s3_df = temp_to_s3_df.withColumn(
            "temp_path",
            F.concat(F.lit("file://"), F.col("temp_path")),
        )

        # Read the temp HL7 files
        df = spark.read.format("hl7").load(temp_hl7_files)

        # Join with the temp to s3 mapping df to get the S3 paths
        df = df.withColumn("temp_path", F.input_file_name())
        df = df.join(temp_to_s3_df, on="temp_path", how="left")
        df = df.drop("temp_path")

        # Filter out rows from unparsable HL7 files
        message_control_id = segment_field("MSH", 10)
        error_paths = [
            row.source_file
            for row in df.filter(message_control_id.isNull())
            .select("source_file")
            .collect()
        ]
        if error_paths:
            # Write error paths to db
            write_errors(
                error_paths,
                "File is not parsable as HL7",
                activity.info().workflow_id,
                activity.info().activity_id,
            )

            # Remove empty / unparsable rows from df
            df = df.filter(message_control_id.isNotNull())

        if df.isEmpty():
            raise ApplicationError("No data extracted from HL7 messages")

        num_hl7 = df.count()
        activity.heartbeat()
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

        activity.heartbeat()
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

        activity.heartbeat()
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

        activity.heartbeat()
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
        activity.heartbeat()
        patient_id_json_df = exploded_patient_id_df.groupBy("source_file").agg(
            F.to_json(
                F.collect_list(
                    F.struct("id_number", "assigning_authority", "identifier_type_code")
                )
            ).alias("patient_id_json")
        )

        activity.heartbeat()
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
        activity.heartbeat()
        activity.logger.info(
            "Creating Delta Lake table %s if it does not exist", report_table_name
        )
        dt = (
            DeltaTable.createIfNotExists(spark)
            .tableName(f"default.{report_table_name}")
            .addColumns(df.schema)
            .partitionedBy("year")
            .execute()
        )

        activity.heartbeat()
        activity.logger.info("Writing data to Delta Lake table %s", report_table_name)
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

        activity.heartbeat()
        success_paths = [row.source_file for row in df.select("source_file").collect()]
        write_successes(
            success_paths, activity.info().workflow_id, activity.info().activity_id
        )

        activity.logger.info("Finished")
        return num_hl7
    except (Py4JError, ConnectionError) as e:
        activity.logger.error(
            "Spark error ingesting HL7 files to Delta Lake. Marking pod unhealthy."
        )
        try:
            message = str(e)
        except:
            message = "Unknown error"

        # Write the error message to the health file
        with health_file.open("a") as f:
            f.write(message + "\n")
        raise
    except TimeoutError:
        activity.logger.info("Temporal activity has been cancelled")
    except Exception as e:
        activity.logger.exception("Error ingesting HL7 files to Delta Lake", exc_info=e)
        raise
    finally:
        if spark is not None:
            activity.logger.info("Stopping spark")
            try:
                spark.stop()
            except:
                pass

        if temp_dir is not None:
            # CLean up temp dir after Spark is finished processing
            activity.logger.info("Cleaning up temp dir %s", temp_dir)
            try:
                shutil.rmtree(temp_dir)
            except Exception as e:
                activity.logger.error("Error cleaning up temp dir %s", e)
