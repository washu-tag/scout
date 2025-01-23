import argparse
import asyncio
import concurrent.futures
import logging
import os
import sys

from temporalio.client import Client
from temporalio.worker import Worker

from temporalpy.activities.ingesthl7 import (
    TASK_QUEUE_NAME,
    ingest_hl7_files_to_delta_lake_activity,
)

log = logging.getLogger("workflow_worker")


async def run_worker(temporal_address: str, namespace: str) -> None:
    client = await Client.connect(temporal_address, namespace=namespace)
    with concurrent.futures.ThreadPoolExecutor() as pool:
        worker = Worker(
            client,
            task_queue=TASK_QUEUE_NAME,
            activities=[ingest_hl7_files_to_delta_lake_activity],
            activity_executor=pool,
        )

        log.info("Starting worker...")
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

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    await run_worker(temporal_address, temporal_namespace)


if __name__ == "__main__":
    asyncio.run(main())
