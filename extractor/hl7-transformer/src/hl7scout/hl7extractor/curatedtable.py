from delta import DeltaTable

from .dataextraction import DerivativeTable
from .sparkutils import (
    filter_df_for_update_inserts,
    merge_df_into_dt_on_source_file,
    empty_string_coalesce,
    create_table_from_df,
)

from pyspark.sql import functions as F
import uuid


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
                "primary_patient_identifier": uuid.uuid4().hex,  # TODO : this obviously doesn't work
            }
        )
        .withColumns(
            {
                "accession_number": F.col("filler_order_number"),
                "primary_study_identifier": F.col("filler_order_number"),
            }
        )
        .drop(
            "orc_3_filler_order_number",
            "obr_3_filler_order_number",
            "orc_3_filler_order_number",
            "obr_3_filler_order_number",
        )
    )

    # Update existing curated table or create it if it does not yet exist
    if spark.catalog.tableExists(table_name):
        merge_df_into_dt_on_source_file(
            DeltaTable.forName(spark, table_name), curated_df
        )
    else:
        create_table_from_df(curated_df, table_name)
