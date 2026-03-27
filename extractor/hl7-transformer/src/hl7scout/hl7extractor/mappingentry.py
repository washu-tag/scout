from dataclasses import dataclass


@dataclass
class MappingEntry:
    scout_patient_id: str
    primary_report_identifier: str
    mpi: str
    epic_mrn: str
    consistent: bool
    existing_mapping: bool

    @classmethod
    def from_df_row(cls, row, existing=False):
        return cls(
            scout_patient_id=row["scout_patient_id"],
            primary_report_identifier=row["primary_report_identifier"],
            mpi=row["mpi"],
            epic_mrn=row["epic_mrn"],
            consistent=row["consistent"],
            existing_mapping=existing,
        )

    def to_dict(self):
        return {
            "scout_patient_id": self.scout_patient_id,
            "primary_report_identifier": self.primary_report_identifier,
            "mpi": self.mpi,
            "epic_mrn": self.epic_mrn,
            "consistent": self.consistent,
        }
