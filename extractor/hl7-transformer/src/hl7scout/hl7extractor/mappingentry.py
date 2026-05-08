import copy
from dataclasses import dataclass
from typing import Optional


@dataclass
class MappingEntry:
    scout_patient_id: str
    primary_report_identifier: str
    mpi: str
    epic_mrn: str
    consistent: bool
    existing_mapping: bool
    previous_scout_patient_id: Optional[str]

    @classmethod
    def from_df_row(cls, row, existing=False):
        return cls(
            scout_patient_id=row["scout_patient_id"],
            primary_report_identifier=row["primary_report_identifier"],
            mpi=row["mpi"],
            epic_mrn=row["epic_mrn"],
            consistent=row["consistent"],
            existing_mapping=existing,
            previous_scout_patient_id=None,
        )

    def to_dict(self):
        return {
            "scout_patient_id": self.scout_patient_id,
            "primary_report_identifier": self.primary_report_identifier,
            "mpi": self.mpi,
            "epic_mrn": self.epic_mrn,
            "consistent": self.consistent,
        }

    def to_dict_history(self):
        as_dict = self.to_dict()
        as_dict["previous_scout_patient_id"] = self.previous_scout_patient_id
        return as_dict

    def prepare_history_copy(self):
        as_copy = copy.deepcopy(self)
        as_copy.previous_scout_patient_id = self.scout_patient_id
        return as_copy
