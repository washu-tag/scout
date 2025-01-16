from temporalio import activity

from temporalpy.hl7extractor.deltalake import import_hl7_files_to_deltalake


TASK_QUEUE_NAME = "ingest-hl7-delta-lake"


@activity.dataclass(frozen=True)
class IngestHl7FilesToDeltaLakeActivityInput:
    deltaTable: str
    hl7Files: list[str]


@activity.dataclass(frozen=True)
class IngestHl7FilesToDeltaLakeActivityOutput:
    pass


@activity.defn
def ingest_hl7_files_to_delta_lake_activity(activity_input: IngestHl7FilesToDeltaLakeActivityInput) -> IngestHl7FilesToDeltaLakeActivityOutput:
    """Ingest HL7 files to Delta Lake."""
    activity.logger.info("Ingesting HL7 files to Delta Lake: %s", activity_input.deltaTable)
    import_hl7_files_to_deltalake(activity_input.deltaTable, activity_input.hl7Files)

    return IngestHl7FilesToDeltaLakeActivityOutput()
