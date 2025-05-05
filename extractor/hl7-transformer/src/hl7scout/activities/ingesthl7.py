from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from temporalio import activity

from hl7scout.hl7extractor.deltalake import import_hl7_files_to_deltalake

TASK_QUEUE_NAME = "ingest-hl7-delta-lake"
ACTIVITY_NAME = "ingest_hl7_files_to_delta_lake"


@dataclass(frozen=True)
class IngestHl7FilesToDeltaLakeActivityInput:
    hl7ManifestFilePath: str
    modalityMapPath: Optional[str] = None
    reportTableName: Optional[str] = None


@dataclass(frozen=True)
class IngestHl7FilesToDeltaLakeActivityOutput:
    numHl7Ingested: int


class IngestHl7FilesActivity:
    """Create an ingest HL7 files to Delta Lake activity.

    By wrapping the activity in a function, several default values can be
    provided once at startup, not for each activity invocation.
    Though they can be overridden for individual invocations if needed.
    """

    default_modality_map_path: str
    default_report_table_name: str
    health_file: Path

    def __init__(
        self,
        default_report_table_name: str,
        default_modality_map_path: str,
        health_file: Path,
    ):
        self.default_modality_map_path = default_modality_map_path
        self.default_report_table_name = default_report_table_name
        self.health_file = health_file

    @activity.defn(name=ACTIVITY_NAME)
    def ingest_hl7_files_to_delta_lake(
        self,
        activity_input: IngestHl7FilesToDeltaLakeActivityInput,
    ) -> IngestHl7FilesToDeltaLakeActivityOutput:
        """Ingest HL7 files to Delta Lake."""
        modality_map_path = (
            activity_input.modalityMapPath or self.default_modality_map_path
        )
        report_table_name = (
            activity_input.reportTableName or self.default_report_table_name
        )
        activity.logger.info("Ingesting HL7 files to Delta Lake: %s", report_table_name)
        num_hl7_ingested = import_hl7_files_to_deltalake(
            activity_input.hl7ManifestFilePath,
            modality_map_path,
            report_table_name,
            self.health_file,
        )

        return IngestHl7FilesToDeltaLakeActivityOutput(num_hl7_ingested)
