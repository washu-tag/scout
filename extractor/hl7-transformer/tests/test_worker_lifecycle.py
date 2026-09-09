"""How the worker process starts, stops, and reports why it stopped.

`test_spark_session.py` covers which failures inside the activity classify as Spark
being unreachable. This file covers what the process does about it: the callback is
handed across a thread boundary, the worker is shut down, and the process exits
nonzero so the container restarts. The same paths decide what happens when the worker
never starts, when the health server dies, and when k8s sends SIGTERM — all of which
have to end the process rather than leave a pod Ready with nothing running in it.

No Temporal server or Spark session is involved; `Client`, `Worker` and the health
server are stubbed, because the subject is the wiring in `ingesthl7worker`.
"""

import asyncio
import signal
import socket
import subprocess
import sys
import threading
import time
from textwrap import dedent
from unittest import mock

import pytest

from hl7scout import ingesthl7worker
from hl7scout.ingesthl7worker import run_worker, main


@pytest.fixture(autouse=True)
def restore_sigterm_handler():
    """`main` installs a process-wide SIGTERM handler; don't leak it into pytest."""
    original = signal.getsignal(signal.SIGTERM)
    yield
    signal.signal(signal.SIGTERM, original)


@pytest.fixture
def health_file(tmp_path):
    """The file whose contents make /healthz report unhealthy. Empty == healthy."""
    path = tmp_path / "health"
    path.touch()
    return path


class FakeWorker:
    """Stands in for temporalio's Worker: run() returns once shutdown() is called.

    It deliberately implements only run/shutdown and not the async context manager
    protocol, so going back to `async with worker` fails these tests rather than
    silently reintroducing the startup hang described above. Reproducing that hang
    faithfully would mean modelling Worker's private shutdown events here, which
    would only test our model of temporalio; `test_sigterm_stops_the_process` is the
    black-box counterpart that catches it against the real thing.
    """

    def __init__(self, *args, run_error=None, **kwargs):
        self._stopped = asyncio.Event()
        self._run_error = run_error
        self.shutdown_calls = 0

    async def run(self):
        if self._run_error is not None:
            raise self._run_error
        await self._stopped.wait()

    async def shutdown(self):
        self.shutdown_calls += 1
        self._stopped.set()


class FakeHealthServer:
    """Stands in for uvicorn.Server: serve() returns once should_exit is set."""

    def __init__(self, serve_error=None):
        self._stopped = asyncio.Event()
        self._serve_error = serve_error
        self.serve_calls = 0

    @property
    def should_exit(self):
        return self._stopped.is_set()

    @should_exit.setter
    def should_exit(self, value):
        if value:
            self._stopped.set()

    async def serve(self):
        self.serve_calls += 1
        if self._serve_error is not None:
            raise self._serve_error
        await self._stopped.wait()


def _patch_worker_deps(worker, captured):
    """Patch out everything `run_worker` needs from the outside world, capturing the
    `on_spark_failure` callback it hands to the activity."""

    class CapturingActivity:
        def __init__(self, table_name, health_file, on_spark_failure=None):
            captured["on_spark_failure"] = on_spark_failure

        def ingest_hl7_files_to_delta_lake(self, *args, **kwargs):
            raise AssertionError("not called in these tests")

    client = mock.AsyncMock()
    return (
        mock.patch.object(ingesthl7worker, "Client", client),
        mock.patch.object(ingesthl7worker, "IngestHl7FilesActivity", CapturingActivity),
        mock.patch.object(ingesthl7worker, "Worker", lambda *a, **kw: worker),
    )


def test_spark_failure_from_an_activity_thread_shuts_the_worker_down(health_file):
    """The activity runs in a thread pool, so its callback fires off the event loop
    and has to be marshalled back onto it. Once it is, the worker stops polling."""
    worker = FakeWorker()
    captured = {}

    async def scenario():
        spark_failure = asyncio.Event()
        patches = _patch_worker_deps(worker, captured)
        with patches[0], patches[1], patches[2]:
            task = asyncio.create_task(
                run_worker("addr", "ns", "reports", health_file, spark_failure)
            )
            # Let run_worker get as far as constructing the activity.
            while "on_spark_failure" not in captured:
                await asyncio.sleep(0)

            # Fire it from a non-loop thread, exactly as the activity executor does.
            thread = threading.Thread(target=captured["on_spark_failure"])
            thread.start()
            thread.join()

            await asyncio.wait_for(task, timeout=5)
        return spark_failure

    spark_failure = asyncio.run(scenario())

    assert spark_failure.is_set()
    assert worker.shutdown_calls == 1
    # Spark being unreachable is the activity's news to report, not the worker's.
    assert health_file.read_text() == ""


def test_worker_startup_failure_is_reported_not_swallowed(health_file):
    """A worker that fails before it starts (the namespace check) used to leave
    run_worker parked in Worker.__aexit__ forever, so nothing marked the pod
    unhealthy and it sat Ready with no worker in it."""
    error = RuntimeError("Namespace nonexistent-ns is not found")
    worker = FakeWorker(run_error=error)
    captured = {}

    async def scenario():
        patches = _patch_worker_deps(worker, captured)
        with patches[0], patches[1], patches[2]:
            await asyncio.wait_for(
                run_worker("addr", "ns", "reports", health_file, asyncio.Event()),
                timeout=5,
            )

    with pytest.raises(RuntimeError, match="nonexistent-ns"):
        asyncio.run(scenario())

    assert "nonexistent-ns" in health_file.read_text()


def test_main_exits_nonzero_after_a_spark_failure():
    """The exit code is the whole point: it is what restarts the container."""
    health_server = FakeHealthServer()

    async def fake_run_worker(*args):
        spark_failure = args[-1]
        spark_failure.set()

    with (
        mock.patch.object(ingesthl7worker, "run_worker", fake_run_worker),
        mock.patch.object(
            ingesthl7worker, "health_check_server", lambda: health_server
        ),
    ):
        assert asyncio.run(asyncio.wait_for(main([]), timeout=5)) == 1

    # The health server has to be told to stop, or the process would outlive its worker.
    assert health_server.serve_calls == 1
    assert health_server.should_exit


def test_main_stops_when_the_health_server_dies():
    """Losing /healthz used to go unnoticed until liveness caught it ~90s later.

    The error has to surface while the worker is still running, so this asserts on
    how long that took: code that waits for the worker first also reports the error
    eventually, once something else tears the process down.
    """
    health_server = FakeHealthServer(serve_error=RuntimeError("address in use"))

    async def blocking_run_worker(*args):
        await asyncio.Event().wait()

    with (
        mock.patch.object(ingesthl7worker, "run_worker", blocking_run_worker),
        mock.patch.object(
            ingesthl7worker, "health_check_server", lambda: health_server
        ),
    ):
        started = time.monotonic()
        with pytest.raises(RuntimeError, match="address in use"):
            asyncio.run(asyncio.wait_for(main([]), timeout=30))
        elapsed = time.monotonic() - started

    assert elapsed < 5, f"took {elapsed:.1f}s to notice the health server had died"


# How long to give the child process to die after SIGTERM. Generous compared to the
# ~0.5s it takes, but well under the 30s default termination grace period whose
# expiry is the failure this test exists to catch.
SIGTERM_DEADLINE = 15

CHILD = dedent(
    """
    import asyncio
    from unittest import mock

    async def blocking_run_worker(*args, **kwargs):
        await asyncio.Event().wait()

    # Stub only the worker, so the real main() and the real uvicorn server - whose
    # signal handling is what broke this - are the things under test.
    with mock.patch("hl7scout.ingesthl7worker.run_worker", blocking_run_worker):
        from hl7scout.ingesthl7worker import main, SigTermException
        try:
            raise SystemExit(asyncio.run(main([])))
        except SigTermException:
            raise SystemExit(0)
    """
)


def _health_endpoint_is_up(port, deadline):
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def test_sigterm_stops_the_process(tmp_path):
    """A rolling update or pod delete sends SIGTERM. The process has to act on it;
    ignoring it until SIGKILL means every redeploy stalls for the grace period and
    kills the in-flight activity instead of shutting it down."""
    script = tmp_path / "worker_under_sigterm.py"
    script.write_text(CHILD)

    proc = subprocess.Popen(
        [sys.executable, str(script)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        # Signal only once uvicorn is listening, so the signal goes through its
        # capture_signals handling rather than straight to our handler.
        if not _health_endpoint_is_up(8000, time.monotonic() + 10):
            pytest.skip("health check server never bound port 8000")

        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=SIGTERM_DEADLINE)
        except subprocess.TimeoutExpired:
            pytest.fail(
                f"process ignored SIGTERM for {SIGTERM_DEADLINE}s; "
                "k8s would have had to SIGKILL it"
            )
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.communicate()
