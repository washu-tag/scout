from delta import DeltaTable

from .derivativetable import DerivativeTable
from .sparkutils import (
    filter_df_for_update_inserts,
    merge_df_into_dt_on_column,
    empty_string_coalesce,
    create_table_from_df,
)

from pyspark.sql import functions as F, Column, DataFrame


def curated_table(base_report_table_name: str) -> DerivativeTable:
    return DerivativeTable(
        source_table=base_report_table_name,
        table_name=f"{base_report_table_name}_curated",
        process_source_data=curate_silver_table,
    )


def curate_silver_table(batch_df, spark, table_name):
    """
    Transforms data from WashU specific to more generic table.
    After curation, the curation table will contain:
    * primary_patient_identifier - a unique ID for the patient
    * primary_study_identifier - a unique ID for the study corresponding to the report
    * accession_number - Accession number of the study corresponding to the report
    * primary_report_identifier - a unique ID for the report
    The derivation of these columns is highly specific to WashU, but the idea
    is that the columns themselves are generic and could be similarly constructed
    for other sites.

    The patient ID is the most complex derivation here and must be noted that this
    scheme does NOT support linking a patient's reports over multiple versions of
    HL7. That is a decision we could revisit at a later date, but the primary reason
    is that doing so would be incredibly complex to write from needing to handle
    patient ID merges and concurrency problems, and it would only be a small win anyway
    to link the small amount of 2.3 reports to 2.7 reports (and leave 2.4 reports isolated).

    To derive a patient ID, this process is designed from assumptions/inferences and
    a good deal of manual inspection of the data. The empirical data is as follows:
    * We have 3 HL7 versions represented in our data: 2.3, 2.4, and 2.7. The IDs look
      different corresponding to their HL7 version.
    * In 2.3, reports have an ID in PID-2 (the legacy field), and sometimes an ID in PID-3.
      We've heard from an expert in the hospital system that this PID-2 value is an "MPI"
      for a patient that matches the "EMPI" ID in 2.7 reports. However, we have found several
      instances of reports such that their MPI are equal, but they have differing PID-3 IDs.
      Because the PID-3 IDs seem unreliable here, we extract a patient ID therefore from only PID-2
    * In 2.4, PID-2 is unused, and each report has up to three IDs. These IDs correspond to exactly
      one of the assigning authorities "BJH", "BJWC", or "SLCH" with the three identifier type codes
      "MR", "EE", or "SS. Because we see data integrity issues again with disagreeing IDs (for example,
      1 value of bjh_ee with 2 different values for bjh_ss and 5 different values for bjh_mr), we pick
      only the particular "MR" ID found in the report.
    * In 2.7, PID-2 has a value, but does not seem like one that users are interested in searching on.
      Reports have an MBMC ID, or an EPIC MRN and optional EMPI ID (corresponding to the 2.3 MPI). Because
      exactly one of the MBMC ID and EPIC MRN seems to be present, we can use that ID for the patient.
    """

    filtered_df = filter_df_for_update_inserts(batch_df)
    if filtered_df is None:
        return

    def extract_patient_id(id_column: str, df: DataFrame) -> Column:
        if id_column in df.columns:
            return F.when(
                F.col(id_column).isNotNull(),
                F.concat_ws("_", F.lit(id_column), F.col(id_column)),
            )
        else:  # particular patient id may not have been seen yet
            return F.lit(None)

    curated_df = (
        filtered_df.withColumnRenamed("source_file", "primary_report_identifier")
        .withColumns(
            {
                "placer_order_number": empty_string_coalesce(
                    "obr_2_placer_order_number", "orc_2_placer_order_number"
                ),
                "filler_order_number": empty_string_coalesce(
                    "obr_3_filler_order_number", "orc_3_filler_order_number"
                ),
                "primary_patient_identifier": F.when(
                    F.col("version_id") == "2.7",
                    F.coalesce(
                        *[
                            extract_patient_id(pat_id, filtered_df)
                            for pat_id in ["epic_mrn", "mbmc_mr", "empi_mr"]
                        ]
                    ),
                )
                .when(
                    F.col("version_id") == "2.4",
                    F.coalesce(
                        *[
                            extract_patient_id(f"{authority}_mr", filtered_df)
                            for authority in ["bjh", "bjwc", "slch"]
                        ]
                    ),
                )
                .otherwise(extract_patient_id("mpi", filtered_df)),
            }
        )
        .withColumns(
            {
                "accession_number": F.col("filler_order_number"),
                "primary_study_identifier": F.col("filler_order_number"),
            }
        )
        .drop(
            "orc_2_placer_order_number",
            "obr_2_placer_order_number",
            "orc_3_filler_order_number",
            "obr_3_filler_order_number",
            "filler_order_number",
        )
    )

    # Update existing curated table or create it if it does not yet exist
    if spark.catalog.tableExists(table_name):
        merge_df_into_dt_on_column(
            DeltaTable.forName(spark, table_name),
            curated_df,
            "primary_report_identifier",
        )
    else:
        create_table_from_df(curated_df, table_name)
