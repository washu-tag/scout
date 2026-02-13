from typing import Optional

from delta import DeltaTable
from pyspark.sql import DataFrame, Column, Window
from pyspark.sql import functions as F


def merge_df_into_dt_on_column(dt: DeltaTable, df: DataFrame, merge_col: str):
    (
        dt.alias("s")
        .merge(
            df.alias("t"),
            f"s.{merge_col} = t.{merge_col} AND s.year = t.year",
        )
        .whenMatchedUpdateAll()
        .whenNotMatchedInsertAll()
        .execute()
    )


def filter_df_for_update_inserts(batch_df: DataFrame) -> Optional[DataFrame]:
    if batch_df.isEmpty():
        return None

    updates_insert_df = batch_df.filter(
        F.col("_change_type").isin(["insert", "update_postimage"])
    ).drop("_change_type", "_commit_version", "_commit_timestamp")

    if updates_insert_df.isEmpty():
        return None

    return updates_insert_df


def dedupe_df_on_accession_number(batch_df: DataFrame) -> Optional[DataFrame]:
    updates_insert_df = filter_df_for_update_inserts(batch_df)

    if updates_insert_df is None:
        return None

    updates_insert_df = updates_insert_df.filter(
        (F.col("accession_number").isNotNull())
        & (F.trim(F.col("accession_number")) != "")
    )

    # First, make sure our new data only has the newest report per accession number
    dedupe_window = Window.partitionBy("accession_number").orderBy(F.desc("message_dt"))
    # We have to create an explicit column instead of filtering by window function
    # even though it looks tempting to put it all in the filter
    return (
        updates_insert_df.withColumn("report_index", F.row_number().over(dedupe_window))
        .filter(F.col("report_index") == 1)
        .drop("report_index")
    )


def create_table_from_df(df: DataFrame, table_name: str):
    (
        df.write.format("delta")
        .option("delta.enableChangeDataFeed", "true")
        .mode("append")
        .saveAsTable(table_name)
    )


def empty_string_coalesce(col1: str, col2: str) -> Column:
    """
    Returns col1 if not null and not empty string, otherwise col2
    """
    c1 = F.col(col1)
    c2 = F.col(col2)

    return F.when(c1 != "", c1).otherwise(c2)
