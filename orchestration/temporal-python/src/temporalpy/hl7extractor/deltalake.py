import argparse
import logging
import os
import sys
from collections.abc import Iterator
from dataclasses import asdict
from typing import Optional, Iterable

import pandas as pd
import pyarrow.compute as pc
from deltalake import DeltaTable, write_deltalake

from temporalpy.hl7extractor.hl7 import MessageData, read_hl7_message

log = logging.getLogger(__name__)
storage_options = {"conditional_put": "etag"}


def read_hl7_directory(directory: str, cache: Optional[set[str]] = None) -> Iterator[MessageData]:
    """Read HL7 messages from directory."""
    cache = cache or set()
    if directory in cache:
        return

    for root, subdir, files in os.walk(directory):
        for file in files:
            path = os.path.join(root, file)
            if path in cache:
                continue
            if file.endswith(".hl7"):
                yield read_hl7_message(path)
                cache.add(path)

    cache.add(directory)


def read_hl7_input(hl7input: Iterable[str]) -> Iterator[MessageData]:
    """Read HL7 messages from input files or directories."""
    cache = set()
    for path in hl7input:
        if path in cache:
            continue
        if os.path.isdir(path):
            yield from read_hl7_directory(path, cache=cache)
        else:
            yield read_hl7_message(path)
            cache.add(path)


def import_hl7_files_to_deltalake(delta_table: str, hl7_input: list[str]) -> Optional[str]:
    """Extract data from HL7 messages and write to Delta Lake."""
    log.info(f"Reading HL7 messages from {hl7_input}")
    df = pd.DataFrame.from_records(asdict(message) for message in read_hl7_input(hl7_input) if message is not None).astype(dtype=str)

    log.info(f"Extracted data from {len(df)} HL7 messages")

    # Extract time column for partitioning
    timestamp = pd.to_datetime(df["msh_7_message_timestamp"], errors="coerce", format="%Y%m%d%H%M%S%f")
    df["year"] = timestamp.dt.year.astype(str)
    df["date"] = timestamp.dt.strftime("%Y-%m-%d")

    table_exists = DeltaTable.is_deltatable(delta_table)

    if not table_exists:
        log.info(f"Creating Delta Lake table {delta_table}")
        write_deltalake(delta_table, df, partition_by=["year"], mode="overwrite", storage_options=storage_options)
        return "success"

    dt = DeltaTable(delta_table, storage_options=storage_options)

    log.info(f"Merging data to Delta Lake table {delta_table}")
    dt.merge(df, predicate="s.source_file = t.source_file", source_alias="s", target_alias="t")\
        .when_matched_update_all()\
        .when_not_matched_insert_all()\
        .execute()

    log.info(f"Confirming write: Reading Delta Lake table from {delta_table}")

    # Read the Delta Lake table back into a DataFrame, filtering to only the values we wrote
    df2 = dt.to_pandas(
        partitions=[
            ("year", "in", df["year"].unique())
        ],
        filters=pc.field("source_file").isin(df["source_file"].values.tolist())
    )

    pd.testing.assert_frame_equal(df.sort_values("source_file", inplace=False).reset_index(drop=True),
                                  df2.sort_values("source_file", inplace=False).reset_index(drop=True))
    return "success"


def delete_delta_table(delta_table: str) -> None:
    """Delete Delta Lake table."""
    log.info(f"Deleting Delta Lake table {delta_table}")
    dt = DeltaTable(delta_table)
    dt.delete()
    dt.vacuum()


def main_cli(argv=None) -> int:
    """Main entry point for the CLI."""
    if argv is None:
        argv = sys.argv[1:]

    parser = argparse.ArgumentParser(
        description="Extract data from HL7 messages and write to Delta Lake",
    )
    parser.add_argument(
        "--delete",
        help="Delete Delta Lake table",
        action="store_true",
    )
    parser.add_argument(
        "--debug",
        help="Turn on debug logging",
        action="store_true",
    )
    parser.add_argument(
        "delta_table",
        help="Path to Delta Lake table",
    )
    parser.add_argument(
        "hl7_input",
        help="HL7 input files or directories",
        nargs="+",
    )

    args = parser.parse_args(argv)
    if args.delete:
        delete_delta_table(args.delta_table)
        return 0

    if args.debug:
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        )
    else:
        logging.basicConfig(
            level=logging.INFO,
            format="%(message)s",
        )

    output = import_hl7_files_to_deltalake(args.delta_table, args.hl7_input)
    if output:
        print(output)
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main_cli())
