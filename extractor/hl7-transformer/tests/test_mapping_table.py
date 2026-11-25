"""Unit tests for the report-to-patient mapping (docs/internal/patient_ids.md)."""

import pytest

from hl7scout.hl7extractor.mappingtableextractor import MappingTableExtractor

_ID_COLUMNS = ("mpi", "empi_mr", "epic_mrn", "bjh_ee")
_BATCH_SCHEMA = (
    "primary_report_identifier string, version_id string, "
    + ", ".join(f"{column} string" for column in _ID_COLUMNS)
    + ", _change_type string, _commit_version long"
)


def _report(report_id, version_id="2.4", **ids):
    return (
        report_id,
        version_id,
        *(ids.get(column) for column in _ID_COLUMNS),
        "insert",
        1,
    )


@pytest.fixture
def extract(spark, request):
    """Returns fn(*reports) that runs one curated change-feed batch through the mapping
    extractor and returns the mapping table as {primary_report_identifier: row}."""
    table_name = f"mapping_{request.node.name}"

    def _extract(*reports):
        batch_df = spark.createDataFrame(list(reports), _BATCH_SCHEMA)
        MappingTableExtractor(spark, table_name).extract(batch_df)
        return {
            row.primary_report_identifier: row
            for row in spark.table(table_name).collect()
        }

    return _extract


def test_reports_without_ids_get_distinct_patient_ids(extract):
    mapping = extract(_report("R1"), _report("R2"), _report("R3"))

    assert len({row.scout_patient_id for row in mapping.values()}) == 3
    for row in mapping.values():
        assert row.scout_patient_id is not None
        assert row.mpi is None and row.epic_mrn is None
        assert row.consistent


def test_report_without_ids_does_not_inherit_existing_patient_id(extract):
    first_id = extract(_report("R1"))["R1"].scout_patient_id

    mapping = extract(_report("R2"))

    assert mapping["R2"].scout_patient_id not in (None, first_id)
    assert mapping["R2"].consistent


def test_reports_with_ids_still_link(extract):
    mapping = extract(
        _report("R1", bjh_ee="M1"),
        _report("R2", bjh_ee="M1"),
        _report("R3", bjh_ee="M2"),
        _report("R4"),
    )
    patient_1 = mapping["R1"].scout_patient_id
    patient_2 = mapping["R3"].scout_patient_id
    assert mapping["R2"].scout_patient_id == patient_1
    assert len({patient_1, patient_2, mapping["R4"].scout_patient_id}) == 3

    mapping = extract(
        _report("R5", bjh_ee="M1"),
        _report("R6", version_id="2.7", empi_mr="M2", epic_mrn="E2"),
    )

    assert mapping["R5"].scout_patient_id == patient_1
    assert mapping["R6"].scout_patient_id == patient_2
    assert all(row.consistent for row in mapping.values())
