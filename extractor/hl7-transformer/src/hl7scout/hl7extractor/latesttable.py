from delta import DeltaTable
from .dataextraction import DerivativeTable
from .sparkutils import dedupe_df_on_accession_number, create_table_from_df
from pyspark.sql import functions as F


def latest_table(base_report_table_name: str) -> DerivativeTable:
    return DerivativeTable(
        source_table=f"{base_report_table_name}_curated",
        table_name=f"{base_report_table_name}_latest",
        process_source_data=process_latest_table,
    )


def process_latest_table(batch_df, spark, table_name):
    """
    Keeps only 1 report per accession number
    """

    deduped_df = dedupe_df_on_accession_number(batch_df)
    if deduped_df is None:
        return

    # Update existing latest table or create it if it does not yet exist
    if spark.catalog.tableExists(table_name):
        latest_dt = DeltaTable.forName(spark, table_name)
        update_set = {
            col_name: F.col(f"s.{col_name}") for col_name in deduped_df.columns
        }

        latest_dt.alias("t").merge(
            deduped_df.alias("s"),
            "t.accession_number = s.accession_number",
        ).whenMatchedUpdate(
            "s.message_dt > t.message_dt",
            update_set,  # Use exact columns as in source
        ).whenNotMatchedInsertAll().execute()  # If no existing row for accession number, insert it as-is
    else:
        create_table_from_df(deduped_df, table_name)
