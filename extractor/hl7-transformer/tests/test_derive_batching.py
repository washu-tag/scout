"""Micro-batch bounding for the derivative cascade.

The derive activity reads the base table's change data feed with
``trigger(availableNow=True)``. With no rate limit configured, Delta's default
admission is ``maxFilesPerTrigger=1000`` and *no byte ceiling*, so a backlog of
several ingest commits — the state the derive lands in whenever ingests outpace
derives — collapses into a single oversized cached micro-batch.

These tests pin the fix: an explicit ``maxBytesPerTrigger`` on every CDF read in
``perform_table_operations``, so a micro-batch never spans more than one source
commit. They assert that *bounding property* rather than a row-count ceiling,
because a Delta commit is the floor of what admission can split: any commit
containing a matched MERGE update materializes ``_change_data`` files and is then
admitted whole regardless of the cap (pinned by
test_an_update_bearing_commit_is_admitted_whole).

Note the fixture drains once before accumulating the backlog. A CDF stream with
no checkpoint reads the table's current state as a single starting *snapshot*,
not as incremental commits, so a backlog test is only meaningful against an
established checkpoint — which is also the production steady state.

create_mapping=False throughout: the mapping derivation dominates suite runtime
and is not the subject here.
"""

import datetime as _dt
from contextlib import contextmanager
from unittest import mock

import pytest
from temporalio.testing import ActivityEnvironment

from conftest import BASE_REPORTS_SCHEMA
from hl7scout.hl7extractor import curatedtable, latesttable
from hl7scout.hl7extractor.deltalake import (
    derive_delta_tables,
    merge_report_df_into_table,
)
from testutils import patched_session

MAX_BYTES_CONF = "spark.scout.derive.maxBytesPerTrigger"

# Commit 0 establishes the table and the streaming checkpoint. The three that
# follow are the backlog: two ingests of new reports, then a correction that
# re-merges commit 0's reports with changed content (so that commit carries
# update_preimage/update_postimage rows, not just inserts).
SEED_COMMIT = [("s3://bucket/a1.hl7", "ACC1"), ("s3://bucket/a2.hl7", "ACC2")]
BACKLOG_COMMITS = [
    [("s3://bucket/b1.hl7", "ACC3"), ("s3://bucket/b2.hl7", "ACC4")],
    [("s3://bucket/c1.hl7", "ACC5"), ("s3://bucket/c2.hl7", "ACC6")],
    [("s3://bucket/a1.hl7", "ACC1X"), ("s3://bucket/a2.hl7", "ACC2X")],
]


def _row(source_file, filler):
    """One synthetic base-reports row. Local rather than the conftest fixture so
    the seeding below can live in a module-scoped fixture (a module-scoped fixture
    cannot depend on function-scoped ones)."""
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
        "diagnoses": [("J18.9", "Pneumonia", "ICD10")],
        "diagnoses_consolidated": "Pneumonia",
        "year": message_dt.year,
    }


def _seed_commit(spark, table, pairs):
    """One MERGE commit into the base table, via the real base-activity path."""
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


@contextmanager
def _record_batches():
    """Wrap the curated and latest processors so every micro-batch they receive is
    recorded. Wrapping rather than replacing keeps the real cascade running, so
    the resulting table contents stay assertable.

    Patching the module globals works because the DerivativeTable factories bind
    ``process_source_data`` at call time, inside the derive."""
    seen = {"curated": [], "latest": []}
    real_curated = curatedtable.curate_silver_table
    real_latest = latesttable.process_latest_table

    def _spy(level, real):
        def wrapper(batch_df, spark, table_name):
            cached = batch_df.cache()
            try:
                rows = cached.select("_commit_version", "_change_type").collect()
                seen[level].append(
                    {
                        "rows": len(rows),
                        "source_versions": sorted({r["_commit_version"] for r in rows}),
                        "change_types": {r["_change_type"] for r in rows},
                    }
                )
                return real(cached, spark, table_name)
            finally:
                cached.unpersist()

        return wrapper

    with (
        mock.patch.object(
            curatedtable, "curate_silver_table", _spy("curated", real_curated)
        ),
        mock.patch.object(
            latesttable, "process_latest_table", _spy("latest", real_latest)
        ),
    ):
        yield seen


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
    """For each variant: seed + drain (establishing the checkpoint), then let a
    three-commit backlog accumulate and derive again with the recording spies.

    Module-scoped — each variant drives the full cascade twice, and that is the
    expensive part of this module."""
    tmp_path = tmp_path_factory.mktemp("batching")
    result = {}
    for name, cap in (("uncapped", None), ("capped", "1k")):
        table = f"reports_batch_{name}"
        assert _seed_commit(spark, table, SEED_COMMIT), "seed must produce a commit"
        with _max_bytes(spark, cap):
            _derive(spark, table, tmp_path)  # establishes the streaming checkpoint

            for pairs in BACKLOG_COMMITS:
                assert _seed_commit(spark, table, pairs), "backlog must commit"
            with _record_batches() as seen:
                _derive(spark, table, tmp_path)

        result[name] = {"table": table, "batches": seen}
    return result


def test_uncapped_derive_coalesces_the_whole_backlog(runs):
    """Baseline, and the failure shape: three accumulated ingest commits arrive as
    a single micro-batch, so cached batch size scales with how far derives have
    fallen behind rather than with one ingest."""
    curated = runs["uncapped"]["batches"]["curated"]

    assert len(curated) == 1, f"expected one coalesced batch, got {curated}"
    assert len(curated[0]["source_versions"]) == len(BACKLOG_COMMITS), (
        "the single batch should span every accumulated base commit; "
        f"got {curated[0]}"
    )


def test_capped_derive_bounds_each_micro_batch_to_one_base_commit(runs):
    """The fix, at the base level: the same backlog drains as one micro-batch per
    ingest commit, so peak batch size is set by a single ingest."""
    curated = runs["capped"]["batches"]["curated"]

    assert len(curated) == len(
        BACKLOG_COMMITS
    ), f"expected one batch per base commit, got {curated}"
    for batch in curated:
        assert (
            len(batch["source_versions"]) == 1
        ), f"no micro-batch may span more than one base commit; got {batch}"
    # every accumulated commit is still processed exactly once, in order
    flattened = [b["source_versions"][0] for b in curated]
    assert flattened == sorted(flattened) == sorted(set(flattened))


def test_cap_applies_to_child_levels_not_just_the_base_stream(runs):
    """`perform_table_operations` recurses, building a fresh CDF reader per level.
    The curated -> latest stream must be bounded too, or the cascade just moves the
    oversized batch one level down."""
    uncapped = runs["uncapped"]["batches"]["latest"]
    capped = runs["capped"]["batches"]["latest"]

    assert len(uncapped) == 1, f"baseline: curated backlog coalesces, got {uncapped}"
    assert (
        len(capped) > 1
    ), f"expected the curated -> latest stream to be bounded too, got {capped}"
    for batch in capped:
        assert len(batch["source_versions"]) == 1, (
            "no latest-level micro-batch may span more than one curated commit; "
            f"got {batch}"
        )


def test_an_update_bearing_commit_is_admitted_whole(runs):
    """Characterization of Delta behaviour the cap cannot override, so nobody later
    reads the cap as a hard byte bound.

    The last backlog commit re-merges existing reports, so it carries CDC files.
    Delta admits such a version whole no matter how small the cap — its pre- and
    post-image rows must therefore land in one micro-batch. Guards against a Delta
    upgrade silently changing this."""
    curated = runs["capped"]["batches"]["curated"]
    update_batches = [b for b in curated if "update_postimage" in b["change_types"]]

    assert (
        len(update_batches) == 1
    ), f"the correction commit should be exactly one micro-batch, got {curated}"
    batch = update_batches[0]
    assert batch["change_types"] >= {
        "update_preimage",
        "update_postimage",
    }, f"pre- and post-image rows must arrive together; got {batch}"
    assert (
        batch["rows"] == len(BACKLOG_COMMITS[-1]) * 2
    ), f"expected a pre/post pair per corrected report, got {batch}"


def test_batching_does_not_change_derivative_contents(spark, runs):
    """Re-batching must be observationally equivalent: splitting the backlog into
    one micro-batch per commit produces the same curated/latest/dx state as
    processing it all at once."""
    assert _contents(spark, runs["capped"]["table"]) == _contents(
        spark, runs["uncapped"]["table"]
    )
