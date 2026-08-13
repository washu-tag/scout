"""Batching the derivative cascade must not change what it produces.

The derive activity reads the base table's change data feed with
``trigger(availableNow=True)`` under an explicit ``maxBytesPerTrigger``, so a
backlog of accumulated ingest commits drains as several micro-batches instead of
one oversized cached batch. This module checks the correctness half of that: the
curated/latest/dx state must come out identical whether the backlog is processed
all at once or one commit at a time.

It deliberately does not assert *how* the backlog is batched -- only that the
batching is observationally invisible.

Note: create_mapping=False throughout. The mapping derivation dominates suite runtime
and is not the subject here.
"""

import datetime as _dt
from contextlib import contextmanager

import pytest
from temporalio.testing import ActivityEnvironment

from conftest import BASE_REPORTS_SCHEMA
from hl7scout.hl7extractor.deltalake import (
    derive_delta_tables,
    merge_report_df_into_table,
)
from testutils import patched_session, table_version

MAX_BYTES_CONF = "spark.scout.derive.maxBytesPerTrigger"

SEED_COMMIT = [("s3://bucket/a1.hl7", "ACC1"), ("s3://bucket/a2.hl7", "ACC2")]
BACKLOG_COMMITS = [
    [("s3://bucket/b1.hl7", "ACC3"), ("s3://bucket/b2.hl7", "ACC4")],
    [("s3://bucket/c1.hl7", "ACC5"), ("s3://bucket/c2.hl7", "ACC6")],
    [("s3://bucket/a1.hl7", "ACC1X"), ("s3://bucket/a2.hl7", "ACC2X")],
]


def _row(source_file, filler):
    message_dt = _dt.datetime(2026, 1, 2, 3, 4, 5)
    return {
        "source_file": source_file,
        "message_control_id": source_file,
        "sending_facility": "TESTFAC",
        "version_id": "2.7",
        "mpi": None,
        "epic_mrn": "EPIC1",
        "empi_mr": None,
        "mbmc_mr": None,
        "orc_2_placer_order_number": "PLC1",
        "obr_2_placer_order_number": "PLC1",
        "orc_3_filler_order_number": filler,
        "obr_3_filler_order_number": filler,
        "birth_date": _dt.date(1980, 1, 1),
        "message_dt": message_dt,
        "requested_dt": message_dt,
        "observation_dt": message_dt,
        "diagnoses": [("J18.9", "Pneumonia, unspecified organism", "I10")],
        "diagnoses_consolidated": "Pneumonia, unspecified organism",
        "year": message_dt.year,
    }


def _seed_commit(spark, table, pairs):
    rows = [
        tuple(_row(sf, filler)[f.name] for f in BASE_REPORTS_SCHEMA.fields)
        for sf, filler in pairs
    ]
    df = spark.createDataFrame(rows, BASE_REPORTS_SCHEMA)
    return merge_report_df_into_table(spark, df, table)


_UNSET = object()


@contextmanager
def _max_bytes(spark, value):
    """Set (or clear) the derive rate limit for the duration of the block."""
    try:
        previous = spark.conf.get(MAX_BYTES_CONF)
    except Exception:
        previous = _UNSET
    if value is None:
        spark.conf.unset(MAX_BYTES_CONF)
    else:
        spark.conf.set(MAX_BYTES_CONF, value)
    try:
        yield
    finally:
        if previous is _UNSET:
            spark.conf.unset(MAX_BYTES_CONF)
        else:
            spark.conf.set(MAX_BYTES_CONF, previous)


def _derive(spark, table, tmp_path):
    with patched_session(spark):
        ActivityEnvironment().run(
            derive_delta_tables, table, False, tmp_path / "health"
        )


def _contents(spark, table):
    """Derivative-table contents, order-independent, for cross-run comparison."""
    curated = spark.table(f"default.{table}_curated").collect()
    latest = spark.table(f"default.{table}_latest").collect()
    dx = spark.table(f"default.{table}_dx").collect()
    return {
        "curated": sorted(
            (r["primary_report_identifier"], r["accession_number"]) for r in curated
        ),
        "latest": sorted(r["accession_number"] for r in latest),
        "dx": sorted((r["accession_number"], r["diagnosis_code"]) for r in dx),
    }


@pytest.fixture(scope="module")
def runs(spark, tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("batching")
    result = {}
    for name, cap in (("uncapped", None), ("capped", "1k")):
        table = f"reports_batch_{name}"
        assert _seed_commit(spark, table, SEED_COMMIT), "seed must produce a commit"
        with _max_bytes(spark, cap):
            _derive(spark, table, tmp_path)  # establishes the streaming checkpoint
            for pairs in BACKLOG_COMMITS:
                assert _seed_commit(spark, table, pairs), "backlog must commit"
            before = table_version(spark, f"{table}_curated")
            _derive(spark, table, tmp_path)
            after = table_version(spark, f"{table}_curated")
        result[name] = {"table": table, "curated_commits": after - before}
    return result


def test_batching_does_not_change_derivative_contents(spark, runs):
    """Splitting the backlog into one micro-batch per commit must produce the same
    curated/latest/dx state as processing it all at once.
    """
    assert runs["capped"]["curated_commits"] != runs["uncapped"]["curated_commits"], (
        "both runs produced the same number of curated commits, so the rate limit "
        f"changed nothing and the comparison below proves nothing: {runs}"
    )
    assert _contents(spark, runs["capped"]["table"]) == _contents(
        spark, runs["uncapped"]["table"]
    )
