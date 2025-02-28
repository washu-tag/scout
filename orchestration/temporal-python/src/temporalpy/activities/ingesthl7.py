from typing import Optional

from temporalio import activity

from temporalpy.hl7extractor.deltalake import import_hl7_files_to_deltalake


TASK_QUEUE_NAME = "ingest-hl7-delta-lake"


@activity.dataclass(frozen=True)
class IngestHl7FilesToDeltaLakeActivityInput:
    deltaTable: str
    hl7Files: list[str]
    modalityMapPath: Optional[str] = None


@activity.dataclass(frozen=True)
class IngestHl7FilesToDeltaLakeActivityOutput:
    pass


def ingest_hl7_files_activity_wrapper(default_modality_map_path: str):
    """Create an ingest HL7 files to Delta Lake activity.

    By wrapping the activity in a function, the default modality map CSV path can be
    provided once at startup, not for each activity invocation.
    Though it can be overridden for individual invocations if needed.
    """

    @activity.defn
    def ingest_hl7_files_to_delta_lake_activity(
        activity_input: IngestHl7FilesToDeltaLakeActivityInput,
    ) -> IngestHl7FilesToDeltaLakeActivityOutput:
        """Ingest HL7 files to Delta Lake."""
        activity.logger.info(
            "Ingesting HL7 files to Delta Lake: %s", activity_input.deltaTable
        )
        modality_map_path = activity_input.modalityMapPath or default_modality_map_path
        import_hl7_files_to_deltalake(
            activity_input.deltaTable, activity_input.hl7Files, modality_map_path
        )

        return IngestHl7FilesToDeltaLakeActivityOutput()

    return ingest_hl7_files_to_delta_lake_activity
