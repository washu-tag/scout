import argparse
import asyncio
import concurrent.futures
import logging
import os
import signal
import sys
from datetime import timedelta
from pathlib import Path

from temporalio.client import Client
from temporalio.worker import Worker

from hl7scout.activities.ingesthl7 import (
    TASK_QUEUE_NAME,
    IngestHl7FilesActivity,
)
from hl7scout.healthapi import health_check_server, HEALTH_TEMP_FILE

log = logging.getLogger("workflow_worker")

# When we are shutting down due to a spark error, wait this long for the
# activity to finish cleanup and reporting its status to temporal
GRACEFUL_SHUTDOWN_TIMEOUT = timedelta(seconds=30)


async def run_worker(
    temporal_address: str,
    namespace: str,
    default_report_delta_table_name: str,
    health_file: Path,
    spark_failure: asyncio.Event,
) -> None:
    """Run the ingest worker until it fails or ``spark_failure`` is set
    by an activity that found Spark unreachable.
    """
    loop = asyncio.get_running_loop()

    def on_spark_failure() -> None:
        # Runs on the activity's worker thread, so hand off to the event loop.
        loop.call_soon_threadsafe(spark_failure.set)

    try:
        client = await Client.connect(temporal_address, namespace=namespace)
        ingest_hl7_files_activity = IngestHl7FilesActivity(
            default_report_delta_table_name,
            health_file,
            on_spark_failure,
        )
        with concurrent.futures.ThreadPoolExecutor(1) as pool:
            worker = Worker(
                client,
                task_queue=TASK_QUEUE_NAME,
                activities=[ingest_hl7_files_activity.ingest_hl7_files_to_delta_lake],
                activity_executor=pool,
                max_cached_workflows=1,
                max_concurrent_workflow_tasks=2,
                max_concurrent_workflow_task_polls=2,
                max_concurrent_activities=1,
                max_concurrent_activity_task_polls=1,
                graceful_shutdown_timeout=GRACEFUL_SHUTDOWN_TIMEOUT,
            )

            async def shutdown_on_spark_failure() -> None:
                await spark_failure.wait()
                log.error(
                    "Spark is unreachable; shutting the worker down so it stops "
                    "taking activity tasks"
                )
                await worker.shutdown()

            log.info("Starting worker. Waiting for activities...")
            shutdown_watcher = asyncio.create_task(shutdown_on_spark_failure())
            try:
                await worker.run()
            finally:
                shutdown_watcher.cancel()
                await asyncio.gather(shutdown_watcher, return_exceptions=True)
            log.info("Worker stopped")
    except Exception as e:
        try:
            message = str(e)
        except:
            message = "Unknown error"

        # Write the error message to the health file
        with health_file.open("a") as f:
            f.write(message + "\n")
        raise


class SigTermException(Exception):
    pass


async def main(argv=None) -> int:
    """Main entry point for the CLI. Returns the process exit code."""
    if argv is None:
        argv = sys.argv[1:]

    parser = argparse.ArgumentParser(
        description="Start a Temporal worker to ingest HL7 files to Delta Lake",
    )
    parser.add_argument(
        "--debug",
        help="Turn on debug logging",
        action="store_true",
    )
    args = parser.parse_args(argv)

    temporal_address = os.environ.get(
        "TEMPORAL_ADDRESS", "temporal-frontend.temporal:7233"
    )
    temporal_namespace = os.environ.get("TEMPORAL_NAMESPACE", "default")
    default_report_delta_table_name = os.environ.get(
        "REPORT_DELTA_TABLE_NAME", "reports"
    )

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # Handle SIGTERM signal to shut down quickly
    def on_sigterm(signum, frame):
        log.info("Received SIGTERM, raising exception")
        raise SigTermException()

    signal.signal(signal.SIGTERM, on_sigterm)

    # Run the health check server and the temporal worker side by side.
    # Whichever stops first ends the process.
    spark_failure = asyncio.Event()
    health_server = health_check_server()
    health_task = asyncio.create_task(health_server.serve(), name="health server")
    worker_task = asyncio.create_task(
        run_worker(
            temporal_address,
            temporal_namespace,
            default_report_delta_table_name,
            HEALTH_TEMP_FILE,
            spark_failure,
        ),
        name="ingest worker",
    )
    try:
        done, _ = await asyncio.wait(
            (health_task, worker_task), return_when=asyncio.FIRST_COMPLETED
        )
        # Read every result so none goes unobserved, then re-raise the first failure.
        errors = [error for task in done if (error := task.exception()) is not None]
        if errors:
            raise errors[0]
    finally:
        # serve() returns only once should_exit is set, so without this the process
        # would keep running with no worker in it.
        health_server.should_exit = True
        worker_task.cancel()
        await asyncio.gather(health_task, worker_task, return_exceptions=True)

    if spark_failure.is_set():
        log.error("Exiting after Spark failure so the container restarts")
    else:
        log.error("The worker or the health check server stopped unexpectedly")
    return 1


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except SigTermException:
        log.info("SIGTERM exception caught, shutting down...")
