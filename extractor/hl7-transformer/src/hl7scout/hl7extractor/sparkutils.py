from typing import Optional

from delta import DeltaTable
from pyspark.sql import DataFrame, Column, Window, SparkSession
from pyspark.sql import functions as F

CONTENT_HASH_COL = "content_hash"
_CONTENT_HASH_EXCLUDED = (CONTENT_HASH_COL, "updated")
# Explicit options so the hash is invariant to session timezone and Spark-default
# drift. ignoreNullFields makes a present-but-all-NULL column (the dynamically
# pivoted patient-ID columns come and go per batch) hash identically to an absent
# one. Never add a MapType column to the hashed set — JSON key order for maps is
# not guaranteed.
_CONTENT_HASH_JSON_OPTIONS = {
    "ignoreNullFields": "true",
    "timeZone": "UTC",
    "timestampFormat": "yyyy-MM-dd'T'HH:mm:ss.SSSSSSXXX",
    "dateFormat": "yyyy-MM-dd",
}


def with_content_hash(df: DataFrame) -> DataFrame:
    """Add a deterministic content_hash column: SHA-256 over the JSON serialization
    of every column except the bookkeeping ones (updated, content_hash itself).
    Column names are sorted so the hash is independent of projection order. The
    hash is never NULL (an all-null row hashes as "{}"), so a NULL content_hash in
    a table can only mean the row predates this column."""
    hashed_cols = sorted(c for c in df.columns if c not in _CONTENT_HASH_EXCLUDED)
    return df.withColumn(
        CONTENT_HASH_COL,
        F.sha2(F.to_json(F.struct(*hashed_cols), _CONTENT_HASH_JSON_OPTIONS), 256),
    )


def merge_df_into_dt_on_column(
    dt: DeltaTable,
    df: DataFrame,
    merge_col: str,
    include_year_condition: bool = True,
    update_condition: Optional[str] = None,
    with_schema_evolution: bool = False,
):
    # Aliases follow MERGE convention (and latesttable.py): t = target Delta table,
    # s = source DataFrame being merged in. update_condition may reference columns
    # as t.<col> / s.<col>.
    builder = dt.alias("t").merge(
        df.alias("s"),
        f"t.{merge_col} = s.{merge_col}{' AND t.year = s.year' if include_year_condition else ''}",
    )
    if with_schema_evolution:
        builder = builder.withSchemaEvolution()
    (
        builder.whenMatchedUpdateAll(condition=update_condition)
        .whenNotMatchedInsertAll()
        .execute()
    )


def filter_df_for_update_inserts(
    batch_df: DataFrame, dedupe_col: str
) -> Optional[DataFrame]:
    if batch_df.isEmpty():
        return None

    updates_insert_df = batch_df.filter(
        F.col("_change_type").isin(["insert", "update_postimage"])
    )

    if updates_insert_df.isEmpty():
        return None

    dedupe_window = Window.partitionBy(dedupe_col).orderBy(F.desc("_commit_version"))

    # content_hash is base-table bookkeeping and must not leak into the derivative
    # cascade: production runs with delta.schema.autoMerge enabled, so a leaked
    # column would silently evolve every derivative schema. This filter is the
    # single entry point for curated/latest/dx batches (drop is a no-op when the
    # column is absent).
    return (
        updates_insert_df.withColumn("dedupe_index", F.row_number().over(dedupe_window))
        .filter(F.col("dedupe_index") == 1)
        .drop(
            "_change_type",
            "_commit_version",
            "_commit_timestamp",
            "dedupe_index",
            CONTENT_HASH_COL,
        )
    )


def dedupe_df_on_accession_number(batch_df: DataFrame) -> Optional[DataFrame]:
    updates_insert_df = filter_df_for_update_inserts(
        batch_df, "primary_report_identifier"
    )

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


def create_table_from_df(
    df: DataFrame, table_name: str, cluster_col: Optional[str] = None
):
    builder = (
        DeltaTable.createIfNotExists(df.sparkSession)
        .tableName(table_name)
        .property("delta.enableChangeDataFeed", "true")
        .addColumns(df.schema)
    )
    if cluster_col:
        # Liquid clustering requires stats on the cluster column, but Delta only
        # collects stats on the first 32 columns by default.
        builder = builder.clusterBy(cluster_col).property(
            "delta.dataSkippingStatsColumns", cluster_col
        )
    builder.execute()
    df.write.format("delta").mode("append").saveAsTable(table_name)


def empty_string_coalesce(col1: str, col2: str) -> Column:
    """
    Returns col1 if not null and not empty string, otherwise col2
    """
    c1 = F.col(col1)
    c2 = F.col(col2)

    return F.when(c1 != "", c1).otherwise(c2)


def extract_from_anticipated_column(
    id_column: str, df: DataFrame, extraction: Optional[Column] = None
) -> Column:
    if extraction is None:
        extraction = F.col(id_column)
    if id_column in df.columns:
        return F.when(
            F.col(id_column).isNotNull(),
            extraction,
        )
    else:  # particular column may not have been seen yet
        return F.lit(None)
