from delta import DeltaTable
from .dataextraction import DerivativeTable
from .sparkutils import dedupe_df_on_accession_number, create_table_from_df
from pyspark.sql import functions as F


def diagnosis_table(base_report_table_name: str) -> DerivativeTable:
    return DerivativeTable(
        source_table=f"{base_report_table_name}_latest",
        table_name=f"{base_report_table_name}_dx",
        process_source_data=process_dx_denormalize,
    )


def process_dx_denormalize(batch_df, spark, table_name):
    """
    Uses latest table and explodes diagnoses to their own rows
    """
    deduped_df = dedupe_df_on_accession_number(batch_df)
    if deduped_df is None:
        return

    exploded_df = (
        deduped_df.withColumn("diagnosis", F.explode("diagnoses"))
        .withColumn("diagnosis_code", F.col("diagnosis.diagnosis_code"))
        .withColumn("diagnosis_code_text", F.col("diagnosis.diagnosis_code_text"))
        .withColumn(
            "diagnosis_code_coding_system",
            F.col("diagnosis.diagnosis_code_coding_system"),
        )
        .drop("diagnosis", "diagnoses", "diagnoses_consolidated")
    )

    # For accession numbers we already have, delete previous diagnoses to replace them
    if spark.catalog.tableExists(table_name):
        accession_numbers = exploded_df.select("accession_number").distinct()
        existing_table = DeltaTable.forName(spark, table_name)

        existing_table.alias("t").merge(
            accession_numbers.alias("s"),
            "t.accession_number = s.accession_number",
        ).whenMatchedDelete().execute()

    create_table_from_df(exploded_df, table_name)
