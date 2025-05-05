import argparse
import asyncio
import concurrent.futures
import logging
import os
import signal
import sys
from pathlib import Path

from temporalio.client import Client
from temporalio.worker import Worker

from hl7scout.activities.ingesthl7 import (
    TASK_QUEUE_NAME,
    IngestHl7FilesActivity,
)
from hl7scout.healthapi import start_spark_health_check_server, SPARK_HEALTH_TEMP_FILE

log = logging.getLogger("workflow_worker")


async def run_worker(
    temporal_address: str,
    namespace: str,
    default_report_delta_table_name: str,
    default_modality_map_path: str,
    health_file: Path,
) -> None:
    client = await Client.connect(temporal_address, namespace=namespace)
    ingest_hl7_files_activity = IngestHl7FilesActivity(
        default_report_delta_table_name,
        default_modality_map_path,
        health_file,
    )
    with concurrent.futures.ThreadPoolExecutor(1) as pool:
        worker = Worker(
            client,
            task_queue=TASK_QUEUE_NAME,
            activities=[ingest_hl7_files_activity.ingest_hl7_files_to_delta_lake],
            activity_executor=pool,
            max_cached_workflows=1,
            max_concurrent_workflow_tasks=1,
            max_concurrent_workflow_task_polls=1,
            max_concurrent_activities=1,
            max_concurrent_activity_task_polls=1,
        )

        log.info("Starting worker. Waiting for activities...")
        await worker.run()
        log.info("Worker stopped")


class SigTermException(Exception):
    pass


async def main(argv=None):
    """Main entry point for the CLI."""
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
    default_modality_map_path = os.environ.get(
        "MODALITY_MAP_PATH", "/data/modality_mapping_codes.csv"
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

    # Start the health check server and the temporal worker
    coros = [
        start_spark_health_check_server(),
        run_worker(
            temporal_address,
            temporal_namespace,
            default_report_delta_table_name,
            default_modality_map_path,
            SPARK_HEALTH_TEMP_FILE,
        ),
    ]

    await asyncio.gather(*coros)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except SigTermException:
        log.info("SIGTERM exception caught, shutting down...")
