"""Unit tests for the hash-gated base-table upsert (issue #482).

`merge_report_df_into_table` owns the whole base upsert: it adds the bookkeeping
columns (updated, content_hash), creates the table if needed, anti-joins away
incoming rows that already exist with identical content (so an identical re-ingest
produces NO commit), and merges the remainder with a conditional matched-update as
a race/retry safety belt.
"""

import datetime

from delta.tables import DeltaTable
from pyspark.sql import functions as F

from conftest import BASE_REPORTS_SCHEMA
from hl7scout.hl7extractor.deltalake import merge_report_df_into_table
from hl7scout.hl7extractor.sparkutils import (
    CONTENT_HASH_COL,
    filter_df_for_update_inserts,
    merge_df_into_dt_on_column,
    with_content_hash,
)
from testutils import cdf_rows_since, table_version, updated_by_source_file


def _single_hash(df) -> str:
    rows = with_content_hash(df).select(CONTENT_HASH_COL).collect()
    assert len(rows) == 1
    return rows[0][0]


def _latest_commit(spark, table):
    """(version, operationMetrics) of the most recent commit."""
    row = (
        spark.sql(f"DESCRIBE HISTORY default.{table}")
        .orderBy(F.desc("version"))
        .first()
    )
    return row.version, row.operationMetrics


# --- with_content_hash properties -----------------------------------------------------


def test_with_content_hash_properties(spark):
    base = spark.createDataFrame(
        [("a.hl7", "CT", 2024)], ["source_file", "val", "year"]
    )

    # `updated` is excluded from the hash
    with_updated = base.withColumn("updated", F.current_timestamp())
    assert _single_hash(base) == _single_hash(with_updated)

    # column order does not matter
    permuted = spark.createDataFrame(
        [(2024, "a.hl7", "CT")], ["year", "source_file", "val"]
    )
    assert _single_hash(base) == _single_hash(permuted)

    # a present-but-all-NULL column hashes the same as an absent one (the dynamically
    # pivoted patient-ID columns come and go per batch)
    with_null_col = base.withColumn("extra_id", F.lit(None).cast("string"))
    assert _single_hash(base) == _single_hash(with_null_col)

    # any value change changes the hash — including empty string vs NULL
    changed = spark.createDataFrame(
        [("a.hl7", "MR", 2024)], ["source_file", "val", "year"]
    )
    empty = spark.createDataFrame([("a.hl7", "", 2024)], ["source_file", "val", "year"])
    null = spark.createDataFrame(
        [("a.hl7", None, 2024)], "source_file string, val string, year int"
    )
    hashes = {_single_hash(df) for df in (base, changed, empty, null)}
    assert len(hashes) == 4

    # never NULL, even for an all-NULL row — a NULL hash in the table can then only
    # mean "legacy row", which is what makes the null-safe backfill airtight
    all_null = spark.createDataFrame([(None, None)], "source_file string, val string")
    assert _single_hash(all_null) is not None


# --- merge_report_df_into_table -------------------------------------------------------


def test_identical_remerge_returns_false_and_no_commit(spark, report_df, report_row):
    table = "reports_merge_noop"
    df = report_df([report_row("s3://x/a.hl7"), report_row("s3://x/b.hl7")])

    assert merge_report_df_into_table(spark, df, table) is True
    version = table_version(spark, table)
    updated = updated_by_source_file(spark, table)

    assert merge_report_df_into_table(spark, df, table) is False
    assert table_version(spark, table) == version
    assert updated_by_source_file(spark, table) == updated


def test_changed_content_updates(spark, report_df, report_row):
    table = "reports_merge_changed"
    merge_report_df_into_table(
        spark,
        report_df([report_row("s3://x/a.hl7"), report_row("s3://x/b.hl7")]),
        table,
    )
    version = table_version(spark, table)
    hash_before = {
        r.source_file: r[CONTENT_HASH_COL]
        for r in spark.table(f"default.{table}").collect()
    }
    updated_before = updated_by_source_file(spark, table)

    changed = report_df(
        [report_row("s3://x/a.hl7", filler="ACC-AMENDED"), report_row("s3://x/b.hl7")]
    )
    assert merge_report_df_into_table(spark, changed, table) is True

    latest_version, metrics = _latest_commit(spark, table)
    assert latest_version > version
    assert metrics["numTargetRowsUpdated"] == "1"
    assert metrics["numTargetRowsInserted"] == "0"

    updates = (
        cdf_rows_since(spark, table, version + 1)
        .filter("_change_type = 'update_postimage'")
        .collect()
    )
    assert {r.source_file for r in updates} == {"s3://x/a.hl7"}

    after = {
        r.source_file: (r[CONTENT_HASH_COL], r.updated)
        for r in spark.table(f"default.{table}").collect()
    }
    assert after["s3://x/a.hl7"][0] != hash_before["s3://x/a.hl7"]
    assert after["s3://x/a.hl7"][1] > updated_before["s3://x/a.hl7"]
    assert after["s3://x/b.hl7"] == (
        hash_before["s3://x/b.hl7"],
        updated_before["s3://x/b.hl7"],
    )


def test_new_source_file_inserts(spark, report_df, report_row):
    table = "reports_merge_insert"
    merge_report_df_into_table(spark, report_df([report_row("s3://x/a.hl7")]), table)

    batch = report_df([report_row("s3://x/a.hl7"), report_row("s3://x/c.hl7")])
    assert merge_report_df_into_table(spark, batch, table) is True

    _, metrics = _latest_commit(spark, table)
    assert metrics["numTargetRowsInserted"] == "1"
    assert metrics["numTargetRowsUpdated"] == "0"
    assert spark.table(f"default.{table}").count() == 2


def test_legacy_null_hash_backfills_exactly_once(spark, report_df, report_row):
    """A table written before content_hash existed gains the column, backfills every
    re-ingested row exactly once (NULL hash never matches null-safely), then goes
    quiet."""
    table = "reports_merge_legacy"
    rows = [report_row("s3://x/a.hl7"), report_row("s3://x/b.hl7")]

    # Build the table the pre-content_hash way: schema has `updated` but no hash.
    legacy_df = report_df(rows).withColumn(
        "updated", F.lit(datetime.datetime(2025, 1, 1, 0, 0, 0))
    )
    dt = (
        DeltaTable.createIfNotExists(spark)
        .tableName(f"default.{table}")
        .property("delta.enableChangeDataFeed", "true")
        .addColumns(legacy_df.schema)
        .partitionedBy("year")
        .execute()
    )
    merge_df_into_dt_on_column(dt, legacy_df, "source_file")
    assert CONTENT_HASH_COL not in spark.table(f"default.{table}").columns

    # Content-identical re-ingest through the new path: one full backfill pass.
    assert merge_report_df_into_table(spark, report_df(rows), table) is True
    assert CONTENT_HASH_COL in spark.table(f"default.{table}").columns
    _, metrics = _latest_commit(spark, table)
    assert metrics["numTargetRowsUpdated"] == "2"
    assert metrics["numTargetRowsInserted"] == "0"
    hashes = [
        r[0] for r in spark.table(f"default.{table}").select(CONTENT_HASH_COL).collect()
    ]
    assert all(h is not None for h in hashes)

    # ... and only once.
    version = table_version(spark, table)
    assert merge_report_df_into_table(spark, report_df(rows), table) is False
    assert table_version(spark, table) == version


def test_mixed_batch_touches_only_new_and_changed(spark, report_df, report_row):
    table = "reports_merge_mixed"
    merge_report_df_into_table(
        spark,
        report_df([report_row("s3://x/a.hl7"), report_row("s3://x/b.hl7")]),
        table,
    )
    version = table_version(spark, table)
    updated_before = updated_by_source_file(spark, table)

    batch = report_df(
        [
            report_row("s3://x/a.hl7"),  # identical
            report_row("s3://x/b.hl7", filler="ACC-AMENDED"),  # changed
            report_row("s3://x/c.hl7"),  # new
        ]
    )
    assert merge_report_df_into_table(spark, batch, table) is True

    _, metrics = _latest_commit(spark, table)
    assert metrics["numTargetRowsUpdated"] == "1"
    assert metrics["numTargetRowsInserted"] == "1"

    changes = cdf_rows_since(spark, table, version + 1)
    assert {
        (r.source_file, r._change_type)
        for r in changes.filter(
            "_change_type IN ('insert', 'update_postimage')"
        ).collect()
    } == {("s3://x/b.hl7", "update_postimage"), ("s3://x/c.hl7", "insert")}
    assert (
        updated_by_source_file(spark, table)["s3://x/a.hl7"]
        == updated_before["s3://x/a.hl7"]
    )


def test_conditional_update_belt_without_prefilter(spark, report_df, report_row):
    """Option A in isolation: even when an identical batch reaches the MERGE itself
    (the pre-filter race/retry window), the conditional matched-update turns it into a
    logical no-op — no updated rows, no CDF update events, no bumped `updated`. NOTE:
    deliberately no assertion on numTargetFilesAdded — copy-on-write MERGE may still
    rewrite matched files; the CDF silence is what protects the derivative cascade."""
    table = "reports_merge_belt"
    df = report_df([report_row("s3://x/a.hl7"), report_row("s3://x/b.hl7")])
    merge_report_df_into_table(spark, df, table)
    version = table_version(spark, table)
    updated_before = updated_by_source_file(spark, table)

    # Same content, fresh `updated` — bypass the anti-join, hit the merge directly.
    source = with_content_hash(df.withColumn("updated", F.current_timestamp()))
    merge_df_into_dt_on_column(
        DeltaTable.forName(spark, f"default.{table}"),
        source,
        "source_file",
        update_condition=f"NOT (t.{CONTENT_HASH_COL} <=> s.{CONTENT_HASH_COL})",
    )

    latest_version, metrics = _latest_commit(spark, table)
    if latest_version > version:
        assert metrics["numTargetRowsUpdated"] == "0"
        assert metrics["numTargetRowsInserted"] == "0"
        update_events = (
            cdf_rows_since(spark, table, version + 1)
            .filter("_change_type != 'insert'")
            .collect()
        )
        assert update_events == []
    assert updated_by_source_file(spark, table) == updated_before


def test_merge_helper_defaults_unchanged(spark, report_df, report_row):
    """The raw helper with default args must keep updating unconditionally — the
    mapping and curated tables rely on exactly today's semantics."""
    table = "reports_merge_helper_defaults"
    df = report_df([report_row("s3://x/a.hl7"), report_row("s3://x/b.hl7")])
    dt = (
        DeltaTable.createIfNotExists(spark)
        .tableName(f"default.{table}")
        .property("delta.enableChangeDataFeed", "true")
        .addColumns(df.schema)
        .partitionedBy("year")
        .execute()
    )
    merge_df_into_dt_on_column(dt, df, "source_file")
    version = table_version(spark, table)

    merge_df_into_dt_on_column(dt, df, "source_file")  # identical, default args

    _, metrics = _latest_commit(spark, table)
    assert metrics["numTargetRowsUpdated"] == "2"  # unconditional update, as before
    update_events = (
        cdf_rows_since(spark, table, version + 1)
        .filter("_change_type = 'update_postimage'")
        .collect()
    )
    assert len(update_events) == 2


def test_filter_drops_content_hash(spark):
    """content_hash must not leak into the derivative cascade: the shared CDF entry
    filter drops it (production sets delta.schema.autoMerge=true, so a leak would
    silently evolve the derivative schemas rather than fail)."""
    cdf = spark.createDataFrame(
        [("s3://x/a.hl7", "somehash", "insert", 1)],
        ["source_file", CONTENT_HASH_COL, "_change_type", "_commit_version"],
    )
    out = filter_df_for_update_inserts(cdf, "source_file")
    assert CONTENT_HASH_COL not in out.columns
    assert out.count() == 1
