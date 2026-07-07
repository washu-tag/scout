"""Unit tests for the derivative-table activity (`derive_delta_tables`).

These seed a synthetic base `reports` Delta table and drive the derivative cascade
through the committed change data feed — the same path production uses, minus HL7
parsing / MinIO / Hive / Trino.
"""

import threading
import time
from concurrent.futures._base import TimeoutError as FuturesTimeoutError
from contextlib import contextmanager
from unittest import mock

import pytest
from py4j.protocol import Py4JError
from temporalio import activity
from temporalio.exceptions import CancelledError
from temporalio.testing import ActivityEnvironment

from hl7scout.hl7extractor.deltalake import derive_delta_tables, spark_activity_session


@contextmanager
def _injected_session(spark):
    """Stand-in for spark_activity_session that yields the test's shared session without
    creating or stopping one (the real CM's getOrCreate would return the session fixture
    and its finally would then stop it for the rest of the suite)."""
    yield spark


def _patched_session(spark):
    """mock.patch replacement for spark_activity_session: ignore the (app_name,
    health_file) args and yield the test session."""
    return mock.patch(
        "hl7scout.hl7extractor.deltalake.spark_activity_session",
        lambda *args, **kwargs: _injected_session(spark),
    )


def _derive(spark, table, *, create_mapping, health_file):
    """Run derive_delta_tables inside an activity context (so activity.heartbeat()/
    logger work), injecting the test's Spark session via spark_activity_session so it
    isn't torn down."""
    with _patched_session(spark):
        ActivityEnvironment().run(
            derive_delta_tables, table, create_mapping, health_file
        )


def _table_version(spark, table):
    """Latest committed Delta version of default.<table> (or None if it doesn't exist)."""
    if not spark.catalog.tableExists(f"default.{table}"):
        return None
    return (
        spark.sql(f"DESCRIBE HISTORY default.{table}")
        .agg({"version": "max"})
        .collect()[0][0]
    )


def test_derivative_happy_path_curated_latest_dx(
    spark, seed_reports, report_row, tmp_path
):
    table = "reports_hp"
    seed_reports(
        table,
        [
            report_row("s3://bucket/a.hl7", filler="ACC1"),
            report_row("s3://bucket/b.hl7", filler="ACC2"),
        ],
    )

    _derive(spark, table, create_mapping=False, health_file=tmp_path / "health")

    curated = spark.table(f"default.{table}_curated")
    latest = spark.table(f"default.{table}_latest")
    dx = spark.table(f"default.{table}_dx")

    assert curated.count() == 2
    assert {
        r.accession_number for r in curated.select("accession_number").collect()
    } == {
        "ACC1",
        "ACC2",
    }
    assert latest.count() == 2
    # one diagnosis per report
    assert dx.count() == 2
    assert {r.diagnosis_code for r in dx.select("diagnosis_code").collect()} == {
        "J18.9"
    }


def test_derivative_failure_does_not_touch_base(
    spark, seed_reports, report_row, tmp_path
):
    """The core fault-isolation guarantee of the split (issue #457): a derivative failure
    must not modify the base table. Inject a failure mid-cascade and assert the base
    `reports` version/row-count are unchanged, then a clean re-run completes."""
    table = "reports_decouple"
    seed_reports(
        table,
        [
            report_row("s3://bucket/a.hl7", filler="ACC1"),
            report_row("s3://bucket/b.hl7", filler="ACC2"),
        ],
    )
    base_version = _table_version(spark, table)
    base_count = spark.table(f"default.{table}").count()

    # Make the first derivative step (curated) blow up partway through the cascade.
    with mock.patch(
        "hl7scout.hl7extractor.curatedtable.curate_silver_table",
        side_effect=RuntimeError("injected derivative failure"),
    ):
        with pytest.raises(Exception):
            _derive(spark, table, create_mapping=False, health_file=tmp_path / "h")

    # Base table is untouched by the failed derivative.
    assert _table_version(spark, table) == base_version
    assert spark.table(f"default.{table}").count() == base_count

    # A clean re-run completes and produces correct derivative tables (resumes from the
    # uncommitted checkpoint batch).
    _derive(spark, table, create_mapping=False, health_file=tmp_path / "h")
    assert spark.table(f"default.{table}_curated").count() == 2


def test_filter_df_for_update_inserts_dedupes_same_row_commits(spark):
    """Wedge regression (the June incident): when one micro-batch's CDF window spans two
    base commits that both touch the same key, the curated source must collapse to one
    row per key — otherwise the curated MERGE throws
    DELTA_MULTIPLE_SOURCE_ROW_MATCHING_TARGET_ROW. Locks the dedup fix in
    filter_df_for_update_inserts."""
    from hl7scout.hl7extractor.sparkutils import filter_df_for_update_inserts

    cdf = spark.createDataFrame(
        [
            ("s3://x/a.hl7", "insert", 63),
            ("s3://x/a.hl7", "update_postimage", 64),  # same key, later commit
            ("s3://x/b.hl7", "insert", 63),
        ],
        ["source_file", "_change_type", "_commit_version"],
    )

    out = filter_df_for_update_inserts(cdf, "source_file")

    rows = out.collect()
    assert len(rows) == 2  # one row per distinct source_file (a collapsed, b kept)
    assert {r.source_file for r in rows} == {"s3://x/a.hl7", "s3://x/b.hl7"}
    # CDF bookkeeping columns are dropped from the deduped output
    assert "_change_type" not in out.columns
    assert "_commit_version" not in out.columns


def test_derivative_is_incremental(spark, seed_reports, report_row, tmp_path):
    """Idempotency/incrementality: a second derive after adding one base row applies only
    the delta (no reprocessing of the first row into a duplicate)."""
    table = "reports_incremental"
    health = tmp_path / "h"

    seed_reports(table, [report_row("s3://bucket/a.hl7", filler="ACC1")])
    _derive(spark, table, create_mapping=False, health_file=health)
    assert spark.table(f"default.{table}_curated").count() == 1

    seed_reports(table, [report_row("s3://bucket/b.hl7", filler="ACC2")])
    _derive(spark, table, create_mapping=False, health_file=health)
    assert spark.table(f"default.{table}_curated").count() == 2


@pytest.mark.flaky(reruns=2)
def test_temporal_cancel_of_running_derivative_leaves_base_untouched(
    spark, seed_reports, report_row, tmp_path, monkeypatch
):
    """Fault isolation under a real Temporal cancellation (issue #457): while the
    derivative activity is running, cancel it; the base table must be untouched and a
    later clean run must still complete.

    Cancellation is delivered reliably (not raced against Spark) by blocking the
    derivative in a heartbeat/is-cancelled loop on the activity thread — env.cancel()
    then makes activity.heartbeat() raise CancelledError (see probe). The loop is
    bounded so a delivery bug fails the test instead of hanging. This test uses the
    passthrough _patched_session, so how the CancelledError is classified is covered
    separately (issue #458, resolved) by the spark_activity_session tests below; here we
    only assert the base-table invariant and resumability.
    """
    table = "reports_cancel"
    seed_reports(table, [report_row("s3://bucket/a.hl7", filler="ACC1")])
    base_version = _table_version(spark, table)
    base_count = spark.table(f"default.{table}").count()

    def _block_until_cancelled(spark_, report_table_name, create_mapping=True):
        # Runs on the activity thread; bounded (<= ~10s) so it can never hang the suite.
        for _ in range(500):
            if activity.is_cancelled():
                raise CancelledError("cancelled")
            activity.heartbeat()  # raises CancelledError once the activity is cancelled
            time.sleep(0.02)
        raise AssertionError("cancellation was never delivered to the derivative")

    monkeypatch.setattr(
        "hl7scout.hl7extractor.deltalake.process_derivative_data",
        _block_until_cancelled,
    )

    env = ActivityEnvironment()

    def _cancel_soon():
        time.sleep(0.3)  # let the derivative get well underway first
        env.cancel()

    canceller = threading.Thread(target=_cancel_soon, daemon=True)
    canceller.start()
    try:
        with _patched_session(spark):
            env.run(derive_delta_tables, table, False, tmp_path / "h")
    except (
        BaseException
    ):  # noqa: BLE001 - cancellation surfaces here; the invariant is what matters
        pass
    finally:
        canceller.join()

    # The cancellation did not touch the base table.
    assert _table_version(spark, table) == base_version
    assert spark.table(f"default.{table}").count() == base_count

    # A subsequent clean run derives correctly (the pipeline is resumable).
    monkeypatch.undo()
    _derive(spark, table, create_mapping=False, health_file=tmp_path / "h")
    assert spark.table(f"default.{table}_curated").count() == 1


# --- spark_activity_session (the shared session/error contract, issue #457) -----------
# These cover the centralized context manager directly, with SparkSession mocked so no
# real session is created or torn down.


def _fake_spark_session_class(fake_session):
    """Return a patch context for deltalake.SparkSession whose builder chain resolves to
    `fake_session`, so the CM's getOrCreate() yields a mock we can assert teardown on.
    """
    patch = mock.patch("hl7scout.hl7extractor.deltalake.SparkSession")
    spark_cls = patch.start()
    (
        spark_cls.builder.appName.return_value.enableHiveSupport.return_value.getOrCreate.return_value
    ) = fake_session
    return patch


def test_spark_activity_session_marks_unhealthy_and_reraises_on_spark_error(tmp_path):
    """A Spark connectivity error is appended to the health file and re-raised; the
    session is still stopped."""
    health = tmp_path / "health"
    fake_session = mock.MagicMock()

    def _use_cm():
        with spark_activity_session("test-cm", health):
            raise Py4JError("gateway gone")

    patch = _fake_spark_session_class(fake_session)
    try:
        with pytest.raises(Py4JError):
            ActivityEnvironment().run(_use_cm)
    finally:
        patch.stop()

    assert "gateway gone" in health.read_text()
    fake_session.stop.assert_called_once()


@pytest.mark.parametrize("exc_type", [Py4JError, TimeoutError, FuturesTimeoutError])
def test_spark_activity_session_cancelled_propagates_without_marking_unhealthy(
    tmp_path, exc_type
):
    """Issue #458: when the activity is cancelled, whatever the torn-down Spark call raises
    — a Py4JError, or either TimeoutError variant — must NOT mark the pod unhealthy and
    must re-raise as a Temporal CancelledError (so the activity is recorded Cancelled, not
    a phantom success and not a retryable failure). The session is still stopped.

    Cancellation is simulated with ActivityEnvironment.cancel() *before* run(), which sets
    the cancelled flag with no thread-injection race, so activity.is_cancelled() is True
    throughout the body — the state the CM now classifies on."""
    health = tmp_path / "health"
    fake_session = mock.MagicMock()

    def _use_cm():
        with spark_activity_session("test-cm", health):
            raise exc_type("torn down by cancel")

    env = ActivityEnvironment()
    env.cancel()
    patch = _fake_spark_session_class(fake_session)
    try:
        with pytest.raises(CancelledError):
            env.run(_use_cm)
    finally:
        patch.stop()

    assert not health.exists()  # no unhealthy mark on cancel
    fake_session.stop.assert_called_once()  # session still torn down


@pytest.mark.parametrize("timeout_exc", [TimeoutError, FuturesTimeoutError])
def test_spark_activity_session_uncancelled_timeout_reraises_for_retry(
    tmp_path, timeout_exc
):
    """A genuine (non-cancellation) timeout is re-raised so Temporal's retry policy fires
    — NOT suppressed as success (the old #458 bug) and NOT treated as a connectivity
    failure (no health-file write). Both the builtin and concurrent.futures TimeoutError
    behave identically now, since the CM no longer catches TimeoutError by name (it keys
    on is_cancelled() instead), so the 3.10-vs-3.11 class distinction no longer matters.
    """
    health = tmp_path / "health"
    fake_session = mock.MagicMock()

    def _use_cm():
        with spark_activity_session("test-cm", health):
            raise timeout_exc("slow py4j call")

    patch = _fake_spark_session_class(fake_session)
    try:
        with pytest.raises(timeout_exc):
            ActivityEnvironment().run(_use_cm)  # not cancelled
    finally:
        patch.stop()

    assert not health.exists()  # a timeout is not a connectivity failure
    fake_session.stop.assert_called_once()


def test_cancelled_derive_raises_instead_of_phantom_success(tmp_path):
    """Activity-level guard (issue #458): a cancelled derive activity must raise (recorded
    Cancelled) rather than return None, which Temporal would record as a successful
    completion. Runs the REAL spark_activity_session (SparkSession mocked) with
    process_derivative_data blowing up as if Spark were torn down mid-derive; asserts a
    CancelledError propagates and the health file is never written."""
    health = tmp_path / "h"
    fake_session = mock.MagicMock()

    env = ActivityEnvironment()
    env.cancel()
    patch = _fake_spark_session_class(fake_session)
    try:
        with mock.patch(
            "hl7scout.hl7extractor.deltalake.process_derivative_data",
            side_effect=Py4JError("torn down mid-derive"),
        ):
            with pytest.raises(CancelledError):
                env.run(derive_delta_tables, "reports", False, health)
    finally:
        patch.stop()

    assert not health.exists()
    fake_session.stop.assert_called_once()
