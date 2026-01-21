from delta import DeltaTable

from .derivativetable import DerivativeTable
from .sparkutils import (
    filter_df_for_update_inserts,
    merge_df_into_dt_on_source_file,
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
    Transform data from WashU specific to more generic table
    """

    filtered_df = filter_df_for_update_inserts(batch_df)
    if filtered_df is None:
        return

    def extract_patient_id(id_column: str, df: DataFrame) -> Column:
        def defensive_null_check():
            if id_column in df.columns:
                return F.col(id_column).isNotNull()
            return F.lit(False)

        return F.when(
            defensive_null_check(),  # particular patient id may not have been seen yet
            F.concat_ws("_", F.lit(id_column), F.col(id_column)),
        )

    curated_df = (
        filtered_df.withColumnRenamed("source_file", "primary_report_identifier")
        .withColumns(
            {
                "placer_order_number": empty_string_coalesce(
                    "orc_2_placer_order_number", "obr_2_placer_order_number"
                ),
                "filler_order_number": empty_string_coalesce(
                    "orc_3_filler_order_number", "obr_3_filler_order_number"
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
        merge_df_into_dt_on_source_file(
            DeltaTable.forName(spark, table_name),
            curated_df,
            "primary_report_identifier",
        )
    else:
        create_table_from_df(curated_df, table_name)
