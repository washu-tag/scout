"""Which failures inside `spark_activity_session` take the worker down.

The pod runs one activity at a time, and an unreachable Spark JVM fails a fresh
activity in about a second — much faster than the liveness probe can restart the pod.
A pod that keeps polling in that state therefore burns every queued workflow's retry
budget before it dies, which is what the `on_spark_failure` callback exists to prevent.
Its whole value is that it fires on *exactly* the Spark-connectivity branch: too broad
and one bad HL7 batch restarts the pod, too narrow and the cascade comes back.

No real Spark session is involved — `SparkSession` is mocked out, since the subject is
the classification branch, not any Spark work.
"""

from unittest import mock

import pytest
from py4j.protocol import Py4JError
from temporalio.exceptions import CancelledError
from temporalio.testing import ActivityEnvironment

from hl7scout.hl7extractor.deltalake import spark_activity_session


@pytest.fixture
def health_file(tmp_path):
    """The file whose contents make /healthz report unhealthy. Empty == healthy."""
    path = tmp_path / "health"
    path.touch()
    return path


def _run_in_session(
    body, *, health_file, on_spark_failure, session_error=None, cancel=False
):
    """Run `body(spark)` inside spark_activity_session in an activity context.
    `session_error` is raised by getOrCreate instead of a session being handed back;
    `cancel` puts the activity in the cancelled state before it starts."""
    env = ActivityEnvironment()
    if cancel:
        env.cancel()

    def activity():
        with spark_activity_session("Test", health_file, on_spark_failure) as spark:
            body(spark)

    with mock.patch("hl7scout.hl7extractor.deltalake.SparkSession") as session_class:
        if session_error is not None:
            builder = (
                session_class.builder.appName.return_value.enableHiveSupport.return_value
            )
            builder.getOrCreate.side_effect = session_error
        env.run(activity)


def test_unreachable_spark_marks_the_pod_unhealthy_and_stops_the_worker(health_file):
    on_spark_failure = mock.Mock()

    with pytest.raises(Py4JError):
        _run_in_session(
            lambda spark: None,
            health_file=health_file,
            on_spark_failure=on_spark_failure,
            session_error=Py4JError("gateway is not connected"),
        )

    assert "gateway is not connected" in health_file.read_text()
    on_spark_failure.assert_called_once_with()


def test_ordinary_activity_failure_leaves_the_worker_polling(health_file):
    """A failure that isn't a Spark connectivity problem is the retry policy's
    business: this pod is fine and must keep taking work."""
    on_spark_failure = mock.Mock()

    def body(spark):
        raise ValueError("unparsable batch")

    with pytest.raises(ValueError):
        _run_in_session(
            body, health_file=health_file, on_spark_failure=on_spark_failure
        )

    assert health_file.read_text() == ""
    on_spark_failure.assert_not_called()


def test_cancellation_does_not_stop_the_worker(health_file):
    """A Temporal cancel that lands mid-Spark surfaces as whatever the torn-down py4j
    call raises (issue #458), so it can look exactly like an unreachable JVM. Spark is
    healthy here — cancelling one activity must not take the worker down with it."""
    on_spark_failure = mock.Mock()

    def body(spark):
        raise Py4JError("connection reset")

    with pytest.raises(CancelledError):
        _run_in_session(
            body,
            health_file=health_file,
            on_spark_failure=on_spark_failure,
            cancel=True,
        )

    assert health_file.read_text() == ""
    on_spark_failure.assert_not_called()
