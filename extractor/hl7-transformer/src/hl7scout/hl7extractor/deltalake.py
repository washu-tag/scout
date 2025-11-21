# I don't know why we have to import this specific exception type, since it is
# literally just an alias for the builtin TimeoutError.
# But if we try to catch the builtin TimeoutError, it doesn't work.
import logging
from concurrent.futures._base import TimeoutError
from collections import defaultdict
from pathlib import Path

from delta.tables import DeltaTable
from py4j.protocol import Py4JError
from pyspark.sql import SparkSession, Column
from pyspark.sql import functions as F
from s3fs import S3FileSystem
from temporalio import activity
from temporalio.exceptions import ApplicationError

from hl7scout.db import write_errors, write_successes
from .schemautils import (
    split_and_transform_repeated_field,
    extract_person_names_from_xpn,
    extract_person_names_from_xcn,
    extract_person_names_from_cnn,
    read_first_struct_name_friendly,
    read_struct_of_names_friendly,
)

import os
import shutil
import tempfile
import zipfile


suffixes = {
    "addendum": ["ADT", "ADN"],
    "findings": ["GDT"],
    "impression": ["IMP"],
    "technician_note": ["TCM"],
}


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


def extract_observation_id_suffix_content(column, suffix_list):
    return F.concat_ws(
        "\n",
        F.collect_list(
            F.when(
                F.split(F.col("obx-3"), "&").getItem(1).isin(suffix_list),
                F.col("obx-5"),
            )
        ),
    ).alias(f"report_section_{column.lower()}")


# Whenever this method is being used, it is partially to emphasize that the objects accessed are *NOT* proper CNN datatypes. We have separated components for OBR-32, OBR-33, OBR-34, and OBR-35 that should be separated sub-components in the standard. In addition, even if we assume that's a simple mistake, there are more sub-components with values than are allowed by the CNN data type.
def extract_people_from_obr_field(column: str) -> Column:
    return extract_person_names_from_cnn(
        column, lambda name: name.family_name.isNotNull()
    )


def import_hl7_files_to_deltalake(
    hl7_manifest_file_path: str,
    modality_map_csv_path: str,
    report_table_name: str,
    health_file: Path,
) -> int:
    """Extract data from HL7 messages and write to Delta Lake."""
    activity_info = activity.info()
    workflow_id = activity_info.workflow_id
    activity_id = activity_info.activity_id
    spark = None
    temp_dir = None
    success_paths = []
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

        temp_dir = tempfile.mkdtemp()
        zip_map = parse_s3_zip_paths(zipped_hl7_file_paths_from_spark)
        activity.logger.info("Downloading and extracting %d zip files", len(zip_map))
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

        activity.heartbeat()
        # Extract the HL7 segments from the smolder objects
        df = (
            df.select(
                "source_file",
                # MSH is stored in its own "message" column
                F.split("message", "\\|").alias("msh"),
                # Other segments are stored in objects in the "segments" column.
                # We need to find the ones we want using their "id" field;
                # for most of them we use the first item in the list.
                *[
                    F.expr(
                        f"filter(segments, x -> x.id = '{segment}')[0].fields"
                    ).alias(segment.lower())
                    for segment in ("PID", "PV1", "ORC", "OBR", "ZDS")
                ],
                # OBX and DG1 are special cases; we need to keep them as lists for now so
                # later we can explode them into separate rows or iterate over them
                F.expr("filter(segments, x -> x.id = 'OBX')").alias("obx_lines"),
                F.expr("filter(segments, x -> x.id = 'DG1')").alias("dg1_lines"),
            )
            .select(
                "source_file",
                "obx_lines",
                "dg1_lines",
                # Extract all the fields where we only want the first component
                *[
                    F.split(F.col(segment).getItem(field - 1), "\\^")
                    .getItem(0)
                    .alias(f"{segment}-{field}")
                    for segment, fields in (
                        ("msh", (4, 7, 10, 12)),
                        ("pid", (2, 7, 8, 10, 22)),
                        ("pv1", (2,)),
                        ("orc", (2, 3)),
                        ("obr", (2, 3, 6, 7, 8, 22, 24)),
                        ("zds", (1,)),
                    )
                    for field in fields
                ],
                # Extract all the fields where we want multiple components
                *[
                    F.split(F.col(segment).getItem(field - 1), "\\^")
                    .getItem(component - 1)
                    .alias(f"{segment}-{field}-{component}")
                    for segment, field, components in (
                        ("pid", 11, (5, 6)),
                        ("obr", 4, (1, 2, 3)),
                    )
                    for component in components
                ],
                # PID-3 and PID-5 are special cases.
                # We keep them as-is for now so we can explode or decompose them later.
                F.col("pid").getItem(2).alias("pid-3"),
                F.col("pid").getItem(4).alias("pid-5"),
                # We need multiple repetitions from OBR-16, OBR-33 and OBR-34.
                # We could handle OBR-32 above, but it's easier to centralize the logic here.
                F.col("obr").getItem(15).alias("obr-16"),
                F.col("obr").getItem(31).alias("obr-32"),
                F.col("obr").getItem(32).alias("obr-33"),
                F.col("obr").getItem(33).alias("obr-34"),
            )
            .cache()
        )

        # Filter out rows from unparsable HL7 files
        null_message_ids = (
            df.filter(F.col("msh-10").isNull()).select("source_file").collect()
        )
        error_paths = [row.source_file for row in null_message_ids]
        if error_paths:
            # Write error paths to db
            write_errors(
                error_paths,
                "File is not parsable as HL7",
                workflow_id,
                activity_id,
            )

            # Remove empty / unparsable rows from df
            df = df.filter(F.col("msh-10").isNotNull())

        if df.isEmpty():
            raise ApplicationError("No data extracted from HL7 messages")

        activity.heartbeat()
        if activity.logger.isEnabledFor(logging.INFO):
            activity.logger.info("Extracted data from %d HL7 messages", df.count())

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
        # We need to explode the OBX segments into separate rows,
        # then get the report text from the OBX-5 field and status from OBX-11
        report_df = (
            df.select(
                "source_file",
                # Get all OBX segments and explode into separate rows, keeping the index as "pos" column
                F.posexplode(F.col("obx_lines")),
            )
            .withColumn("obx", F.col("col").getField("fields"))
            .select(
                "source_file",
                # "pos" holds the index of the OBX segment
                "pos",
                # "obx" holds the OBX segment data
                F.when(
                    # TX data type has a ~ delimiter, replace with newline
                    F.col("obx").getItem(1) == "TX",
                    F.regexp_replace(F.col("obx").getItem(4), "~", "\n"),
                )
                # Other data types are a single line as the value
                .otherwise(F.col("obx").getItem(4)).alias("obx-5"),
                F.col("obx").getItem(2).alias("obx-3"),
                F.col("obx").getItem(10).alias("obx-11"),
            )
            .groupby("source_file")
            .agg(
                # Join all lines of report text into one string
                F.concat_ws("\n", F.collect_list("obx-5")).alias("report_text"),
                # Assume report statuses are the same, pick first
                F.first("obx-11").alias("report_status"),
                *[
                    extract_observation_id_suffix_content(column, suffix_list)
                    for column, suffix_list in suffixes.items()
                ],
            )
        )

        activity.heartbeat()
        activity.logger.info("Creating diagnosis df")
        diagnosis_df = (
            df.select("source_file", "dg1_lines")
            .withColumn(
                "diagnoses",
                F.transform(
                    F.transform(
                        F.col("dg1_lines"), lambda item: F.split(item.fields[2], "\\^")
                    ),
                    lambda parts: F.struct(
                        parts[0].alias("diagnosis_code"),
                        parts[1].alias("diagnosis_code_text"),
                        parts[2].alias("diagnosis_code_coding_system"),
                    ),
                ),
            )
            .drop("dg1_lines")
        )

        activity.heartbeat()
        activity.logger.info("Creating physician df")
        name_df = (
            df.select("source_file", "pid-5", "obr-16", "obr-32", "obr-33", "obr-34")
            .withColumn("full_patient_name", extract_person_names_from_xpn("pid-5"))
            .withColumn(
                "patient_name",
                read_first_struct_name_friendly("full_patient_name"),
            )
            .withColumn(
                "full_ordering_provider", extract_person_names_from_xcn("obr-16")
            )
            .withColumn(
                "ordering_provider",
                read_first_struct_name_friendly("full_ordering_provider"),
            )
            .withColumn(
                "full_principal_result_interpreter",
                extract_people_from_obr_field("obr-32").getItem(0),
            )
            .withColumn(
                "principal_result_interpreter",
                F.concat_ws(
                    " ",
                    F.col("full_principal_result_interpreter.given_name"),
                    F.col("full_principal_result_interpreter.family_name"),
                ),
            )
            .withColumn(
                "full_assistant_result_interpreter",
                extract_people_from_obr_field("obr-33"),
            )
            .withColumn(
                "assistant_result_interpreter",
                read_struct_of_names_friendly("full_assistant_result_interpreter"),
            )
            .withColumn(
                "full_technician",
                extract_people_from_obr_field("obr-34"),
            )
            .withColumn("technician", read_struct_of_names_friendly("technician"))
            .drop("pid-5", "obr-16", "obr-32", "obr-33", "obr-34")
        )

        activity.heartbeat()
        activity.logger.info("Extracting patient id columns")
        patient_ids_df = (
            df.select("source_file", "pid-3")
            .withColumn(
                "patient_ids",
                split_and_transform_repeated_field(
                    "pid-3",
                    lambda parts: F.struct(
                        F.coalesce(parts[0], F.lit("")).alias("id_number"),
                        F.coalesce(parts[3], F.lit("")).alias("assigning_authority"),
                        F.coalesce(parts[4], F.lit("")).alias("identifier_type_code"),
                        F.coalesce(parts[5], F.lit("")).alias("assigning_facility"),
                    ),
                ),
            )
            .drop("pid-3")
            .cache()
        )

        activity.heartbeat()
        activity.logger.info("Creating patient id df")
        patient_id_df = (
            patient_ids_df.select(
                "source_file", F.explode("patient_ids").alias("patient_id")
            )
            .filter(
                "patient_id.id_number != '' AND (patient_id.assigning_facility != '' OR (patient_id.assigning_authority != '' AND patient_id.identifier_type_code != ''))"
            )
            .select(
                "source_file",
                F.col("patient_id.id_number").alias("id_number"),
                F.when(
                    (F.col("patient_id.assigning_authority") != "")
                    & (F.col("patient_id.identifier_type_code") != ""),
                    F.concat_ws(
                        "_",
                        F.lower("patient_id.assigning_authority"),
                        F.lower("patient_id.identifier_type_code"),
                    ),
                )
                .otherwise(
                    F.concat(
                        F.lit("legacy_patient_id_"),
                        F.lower("patient_id.assigning_facility"),
                    )
                )
                .alias("patient_id_col_name"),
            )
            .groupBy("source_file")
            .pivot(
                "patient_id_col_name"
            )  # Turn the patient_id_col_name values into columns
            .agg(
                F.first("id_number")
            )  # Assume they only have one patient id for each type
        )

        activity.heartbeat()
        activity.logger.info("Building final report DataFrame")
        df = (
            df.select(
                # Assign human-readable names
                F.col("msh-10").alias("message_control_id"),
                F.col("msh-4").alias("sending_facility"),
                F.col("msh-12").alias("version_id"),
                F.when(F.col("pid-2") == "", None)
                .otherwise(F.col("pid-2"))
                .alias("mpi"),
                F.col("pid-8").alias("sex"),
                F.col("pid-10").alias("race"),
                F.col("pid-11-5").alias("zip_or_postal_code"),
                F.col("pid-11-6").alias("country"),
                F.col("pid-22").alias("ethnic_group"),
                F.col("pv1-2").alias("patient_class"),
                F.col("orc-2").alias("orc_2_placer_order_number"),
                F.col("obr-2").alias("obr_2_placer_order_number"),
                F.col("orc-3").alias("orc_3_filler_order_number"),
                F.col("obr-3").alias("obr_3_filler_order_number"),
                F.col("obr-4-1").alias("service_identifier"),
                F.col("obr-4-2").alias("service_name"),
                F.col("obr-4-3").alias("service_coding_system"),
                F.col("obr-24").alias("diagnostic_service_id"),
                F.col("zds-1").alias("study_instance_uid"),
                # Create date and time objects
                F.to_date(
                    F.substring(F.col("pid-7"), 1, 8),
                    "yyyyMMdd",
                ).alias("birth_date"),
                *[
                    F.to_timestamp(
                        F.when(
                            F.length(F.col(timestamp_col)) == 12,
                            F.concat(F.col(timestamp_col), F.lit("00")),
                        ).otherwise(F.col(timestamp_col)),
                        "yyyyMMddHHmmss",
                    ).alias(alias)
                    for timestamp_col, alias in (
                        ("msh-7", "message_dt"),
                        ("obr-6", "requested_dt"),
                        ("obr-7", "observation_dt"),
                        ("obr-8", "observation_end_dt"),
                        ("obr-22", "results_report_status_change_dt"),
                    )
                ],
                F.expr("CAST(datediff(YEAR, birth_date, requested_dt) AS INT)").alias(
                    "patient_age"
                ),
                "source_file",
            )
            .join(report_df, "source_file", "left")
            .join(diagnosis_df, "source_file", "left")
            .join(patient_ids_df, "source_file", "left")
            .join(patient_id_df, "source_file", "left")
            .join(name_df, "source_file", "left")
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

        activity.logger.info("Finished writing to delta lake")

        activity.heartbeat()
        success_paths = [row.source_file for row in df.select("source_file").collect()]

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
        return 0
    except Exception as e:
        activity.logger.exception("Error ingesting HL7 files to Delta Lake", exc_info=e)
        raise
    finally:
        if spark is not None:
            activity.logger.info("Clearing spark cache")
            activity.heartbeat()
            try:
                spark.catalog.clearCache()
            except Exception as e:
                activity.logger.error("Error clearing spark cache", exc_info=e)

            activity.logger.info("Stopping spark")
            activity.heartbeat()
            try:
                spark.stop()
            except:
                activity.logger.error("Error stopping spark", exc_info=e)

        if temp_dir is not None:
            # CLean up temp dir after Spark is finished processing
            activity.logger.info("Cleaning up temp dir %s", temp_dir)
            activity.heartbeat()
            try:
                shutil.rmtree(temp_dir)
            except Exception as e:
                activity.logger.error("Error cleaning up temp dir %s", exc_info=e)

    activity.heartbeat()
    write_successes(success_paths, workflow_id, activity_id)

    activity.logger.info("All done")
    return len(success_paths)
