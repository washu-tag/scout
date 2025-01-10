import asyncio

from temporalio.client import Client
from temporalio.worker import Worker

from temporalpy.activities.ingesthl7 import TASK_QUEUE_NAME, ingest_hl7_files_to_delta_lake


async def run_worker(temporal_address: str, namespace: str) -> None:
    client = await Client.connect(args.temporal_address, namespace=args.namespace)
    worker = Worker(
        client,
        task_queue=TASK_QUEUE_NAME,
        activities=[ingest_hl7_files_to_delta_lake],
    )
    await worker.run()


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
    parser.add_argument(
        "temporal_address",
        help="Temporal service address",
    )
    parser.add_argument(
        "namespace",
        help="Temporal namespace",
        default="default",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    await run_worker(args.temporal_address, args.namespace)


if __name__ == "__main__":
    asyncio.run(main())
