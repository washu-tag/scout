from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from temporalio import activity

from hl7scout.hl7extractor.deltalake import (
    import_hl7_files_to_deltalake,
    derive_delta_tables as derive_delta_tables_impl,
)

TASK_QUEUE_NAME = "ingest-hl7-delta-lake"
ACTIVITY_NAME = "ingest_hl7_files_to_delta_lake"
DERIVE_ACTIVITY_NAME = "derive_delta_tables"
MODALITY_MAP_PATH = "/config/modality_mapping_codes.csv"


@dataclass(frozen=True)
class IngestHl7FilesToDeltaLakeActivityInput:
    hl7ManifestFilePath: str
    reportTableName: Optional[str] = None


@dataclass(frozen=True)
class IngestHl7FilesToDeltaLakeActivityOutput:
    numHl7Ingested: int


@dataclass(frozen=True)
class DeriveDeltaTablesActivityInput:
    reportTableName: Optional[str] = None
    # When False, skip the report-patient mapping table derivation and the epic views
    # that depend on it. Defaults to True (production behavior).
    createMapping: Optional[bool] = None


class IngestHl7FilesActivity:
    """Create the ingest-HL7-to-Delta-Lake activities.

    By wrapping the activities in a class, several default values can be provided once at
    startup, not for each activity invocation. They can still be overridden per
    invocation if needed.

    The work is split into two independently-retryable activities (issue #457):
      * ``ingest_hl7_files_to_delta_lake`` — parse HL7 and merge into the base table.
      * ``derive_delta_tables`` — derive curated/latest/dx/mapping tables (and epic
        views) from the base table's committed change data feed. A failure here does not
        re-run the base merge.
    """

    default_report_table_name: str
    health_file: Path

    def __init__(
        self,
        default_report_table_name: str,
        health_file: Path,
    ):
        self.default_report_table_name = default_report_table_name
        self.health_file = health_file

    @activity.defn(name=ACTIVITY_NAME)
    def ingest_hl7_files_to_delta_lake(
        self,
        activity_input: IngestHl7FilesToDeltaLakeActivityInput,
    ) -> IngestHl7FilesToDeltaLakeActivityOutput:
        """Ingest HL7 files into the base Delta Lake report table."""
        report_table_name = (
            activity_input.reportTableName or self.default_report_table_name
        )
        activity.logger.info("Ingesting HL7 files to Delta Lake: %s", report_table_name)
        num_hl7_ingested = import_hl7_files_to_deltalake(
            activity_input.hl7ManifestFilePath,
            MODALITY_MAP_PATH,
            report_table_name,
            self.health_file,
        )

        return IngestHl7FilesToDeltaLakeActivityOutput(num_hl7_ingested)

    @activity.defn(name=DERIVE_ACTIVITY_NAME)
    def derive_delta_tables(
        self,
        activity_input: DeriveDeltaTablesActivityInput,
    ) -> None:
        """Derive the curated/latest/dx/mapping tables from the base report table."""
        report_table_name = (
            activity_input.reportTableName or self.default_report_table_name
        )
        # Default null/unset to True; explicit False skips mapping derivation.
        create_mapping = (
            True
            if activity_input.createMapping is None
            else activity_input.createMapping
        )
        activity.logger.info("Deriving delta tables from: %s", report_table_name)
        derive_delta_tables_impl(
            report_table_name,
            create_mapping=create_mapping,
            health_file=self.health_file,
        )
