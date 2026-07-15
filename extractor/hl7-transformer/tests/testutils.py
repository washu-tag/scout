"""Shared helpers for tests that drive the base-table merge path.

Kept out of conftest.py so they can be imported explicitly (pytest puts this
directory on sys.path). test_derive.py predates this module and keeps its own
local copies of the session helpers.
"""

from contextlib import contextmanager
from unittest import mock


@contextmanager
def injected_session(spark):
    """Stand-in for spark_activity_session that yields the test's shared session
    without creating or stopping one (the real CM's getOrCreate would return the
    session fixture and its finally would then stop it for the rest of the suite)."""
    yield spark


def patched_session(spark):
    """mock.patch replacement for spark_activity_session: ignore the (app_name,
    health_file) args and yield the test session."""
    return mock.patch(
        "hl7scout.hl7extractor.deltalake.spark_activity_session",
        lambda *args, **kwargs: injected_session(spark),
    )


def table_version(spark, table):
    """Latest committed Delta version of default.<table> (or None if it doesn't
    exist)."""
    if not spark.catalog.tableExists(f"default.{table}"):
        return None
    return (
        spark.sql(f"DESCRIBE HISTORY default.{table}")
        .agg({"version": "max"})
        .collect()[0][0]
    )


def cdf_rows_since(spark, table, from_version):
    """All change-data-feed rows of default.<table> starting at from_version
    (inclusive). Callers must ensure from_version <= latest version."""
    return (
        spark.read.format("delta")
        .option("readChangeFeed", "true")
        .option("startingVersion", from_version)
        .table(f"default.{table}")
    )


def updated_by_source_file(spark, table):
    """Map of source_file -> updated timestamp for every row of default.<table>."""
    return {
        r.source_file: r.updated
        for r in spark.table(f"default.{table}")
        .select("source_file", "updated")
        .collect()
    }
