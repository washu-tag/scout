import argparse
import asyncio
import concurrent.futures
import logging
import multiprocessing
import os
import sys

from temporalio.client import Client
from temporalio.worker import Worker, SharedStateManager

from temporalpy.activities.ingesthl7 import (
    TASK_QUEUE_NAME,
    IngestHl7FilesActivity,
)

log = logging.getLogger("workflow_worker")


async def run_worker(
    temporal_address: str, namespace: str, mapping_file_path: str
) -> None:
    client = await Client.connect(temporal_address, namespace=namespace)
    ingest_hl7_files_activity = IngestHl7FilesActivity(mapping_file_path)
    with concurrent.futures.ProcessPoolExecutor(1) as pool:
        worker = Worker(
            client,
            task_queue=TASK_QUEUE_NAME,
            activities=[ingest_hl7_files_activity.ingest_hl7_files_to_delta_lake],
            activity_executor=pool,
            shared_state_manager=SharedStateManager.create_from_multiprocessing(
                multiprocessing.Manager()
            ),
        )

        log.info("Starting worker. Waiting for activities...")
        await worker.run()
        log.info("Worker stopped")


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
    modality_map_path = os.environ.get(
        "MODALITY_MAP_PATH", "/data/modality_mapping_codes.csv"
    )

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    await run_worker(temporal_address, temporal_namespace, modality_map_path)


if __name__ == "__main__":
    asyncio.run(main())
