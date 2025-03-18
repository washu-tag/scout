from dataclasses import dataclass
from typing import Optional

from temporalio import activity

from temporalpy.hl7extractor.deltalake import import_hl7_files_to_deltalake

TASK_QUEUE_NAME = "ingest-hl7-delta-lake"
ACTIVITY_NAME = "ingest_hl7_files_to_delta_lake"


@dataclass(frozen=True)
class IngestHl7FilesToDeltaLakeActivityInput:
    deltaTable: str
    hl7FilePathFiles: list[str]
    modalityMapPath: Optional[str] = None


@dataclass(frozen=True)
class IngestHl7FilesToDeltaLakeActivityOutput:
    numHl7Ingested: int


class IngestHl7FilesActivity:
    """Create an ingest HL7 files to Delta Lake activity.

    By wrapping the activity in a function, the default modality map CSV path can be
    provided once at startup, not for each activity invocation.
    Though it can be overridden for individual invocations if needed.
    """

    default_modality_map_path: str

    def __init__(self, default_modality_map_path: str):
        self.default_modality_map_path = default_modality_map_path

    @activity.defn(name=ACTIVITY_NAME)
    def ingest_hl7_files_to_delta_lake(
        self,
        activity_input: IngestHl7FilesToDeltaLakeActivityInput,
    ) -> IngestHl7FilesToDeltaLakeActivityOutput:
        """Ingest HL7 files to Delta Lake."""
        activity.logger.info(
            "Ingesting HL7 files to Delta Lake: %s", activity_input.deltaTable
        )
        modality_map_path = (
            activity_input.modalityMapPath or self.default_modality_map_path
        )
        num_hl7_ingested = import_hl7_files_to_deltalake(
            activity_input.deltaTable,
            activity_input.hl7FilePathFiles,
            modality_map_path,
        )

        return IngestHl7FilesToDeltaLakeActivityOutput(num_hl7_ingested)
