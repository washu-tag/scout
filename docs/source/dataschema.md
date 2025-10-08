# Data Schema

The Scout Rad Report Explorer is backed by [Delta Lake](https://delta.io/) and [MinIO](https://min.io/) to store 
and manage HL7 radiology reports and other data and metadata. There is one main table that contains the report data. 
Below is a description of the report table schema and the mapping of HL7 fields to the report table columns. Note that 
some fields are derived from others, and some fields may not be directly mapped to HL7 fields.

| Column Name                       | HL7 Report Field | Data Type       | Nullable | Description/Notes                                                                                                                                                                                                          |
|-----------------------------------|------------------|-----------------|----------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `source_file`                     |                  | string          | No       | The location of the report file.                                                                                                                                                                                           |
| `updated`                         |                  | timestamp       | No       | Timestamp of the last update to the report in the Delta Lake.                                                                                                                                                              |
| `message_control_id`              | MSH-10           | string          | Yes      | Unique identifier for the HL7 message.                                                                                                                                                                                     |
| `sending_facility`                | MSH-4            | string          | Yes      | Facility that sent the HL7 message.                                                                                                                                                                                        |
| `version_id`                      | MSH-12           | string          | Yes      | HL7 version used in the message.                                                                                                                                                                                           |
| `message_dt`                      | MSH-7            | timestamp       | Yes      | Date and time the message was created.                                                                                                                                                                                     |
| `mpi`                             | PID-2            | string          | Yes      | Legacy MPI for patient.                                                                                                                                                                                                    |
| `birth_date`                      | PID-7            | date            | Yes      | Patient’s date of birth.                                                                                                                                                                                                   |
| `sex`                             | PID-8            | string          | Yes      | Patient’s gender.                                                                                                                                                                                                          |
| `race`                            | PID-10           | string          | Yes      | Patient’s race.                                                                                                                                                                                                            |
| `zip_or_postal_code`              | PID-11.5         | string          | Yes      | Patient’s ZIP or postal code.                                                                                                                                                                                              |
| `country`                         | PID-11.6         | string          | Yes      | Patient’s country.                                                                                                                                                                                                         |
| `ethnic_group`                    | PID-22           | string          | Yes      | Patient’s ethnicity.                                                                                                                                                                                                       |
| `patient_ids`                     | PID-3            | array of struct | Yes      | Structured representation of all patient identifiers. Patient ID columns are also created for each assigning authority (e.g., `epic_mrn`) or assigning facility. See {ref}`Patient IDs <patient_ids_ref>` for more detail. |
| `epic_mrn`                        |                  | string          | Yes      | Patient ID from Epic system.                                                                                                                                                                                               |
| `orc_2_placer_order_number`       | ORC-2            | string          | Yes      | Placer order number from the order control segment.                                                                                                                                                                        |
| `obr_2_placer_order_number`       | OBR-2            | string          | Yes      | Placer order number from the observation request segment.                                                                                                                                                                  |
| `orc_3_filler_order_number`       | ORC-3            | string          | Yes      | Filler order number from the order control segment.                                                                                                                                                                        |
| `obr_3_filler_order_number`       | OBR-3            | string          | Yes      | Filler order number from the observation request segment.                                                                                                                                                                  |
| `service_identifier`              | OBR-4.1          | string          | Yes      | Code for the service or exam.                                                                                                                                                                                              |
| `service_name`                    | OBR-4.2          | string          | Yes      | Name of the service or exam.                                                                                                                                                                                               |
| `service_coding_system`           | OBR-4.3          | string          | Yes      | Coding system used for the service identifier.                                                                                                                                                                             |
| `diagnostic_service_id`           | OBR-24           | string          | Yes      | Identifier for the diagnostic service.                                                                                                                                                                                     |
| `modality`                        | Derived          | string          | Yes      | Modality of the exam (e.g., CT, MRI).                                                                                                                                                                                      |
| `requested_dt`                    | OBR-6            | timestamp       | Yes      | Date and time the service was requested.                                                                                                                                                                                   |
| `patient_age`                     | Derived          | integer         | Yes      | Patient age at time of report as calculated between `birth_date` and `requested_dt`.                                                                                                                                       |
| `observation_dt`                  | OBR-7            | timestamp       | Yes      | Date and time the observation was made.                                                                                                                                                                                    |
| `observation_end_dt`              | OBR-8            | timestamp       | Yes      | Date and time the observation ended.                                                                                                                                                                                       |
| `results_report_status_change_dt` | OBR-22           | timestamp       | Yes      | Date and time the report status changed.                                                                                                                                                                                   |
| `principal_result_interpreter`    | OBR-32           | string          | Yes      | Name within principal result interpreter field in format "FIRST LAST".                                                                                                                                                     |
| `assistant_result_interpreter`    | OBR-33           | array of string | Yes      | Array of names within assistant result interpreter field in format "FIRST LAST". Duplicates and empty names are filtered out.                                                                                              |
| `technician`                      | OBR-34           | array of string | Yes      | Array of names within technician field in format "FIRST LAST". Duplicates and empty names are filtered out.                                                                                                                |
| `diagnoses`                       | DG1-3            | array of struct | Yes      | Diagnosis codes for the report. See {ref}`Diagnoses <diagnoses_ref>` for more detail.                                                                                                                                      |
| `study_instance_uid`              | ZDS-1            | string          | Yes      | Unique identifier for the study instance.                                                                                                                                                                                  |
| `report_text`                     | OBX-5            | string          | Yes      | Full text of the diagnostic report. See {ref}`Report Text <report_text_ref>` for more detail.                                                                                                                              |
| `report_status`                   | OBX-11           | string          | Yes      | Status of the diagnostic report.                                                                                                                                                                                           |
| `year`                            | Derived          | integer         | Yes      | Year the message was created, derived from `message_dt`.                                                                                                                                                                   |


(patient_ids_ref)=
# Patient IDs
The `patient_ids` column contains an array of structs to represent all patient identifiers associated with the report.

An example of the column with schema is shown below:

```text
+-----------------------------------------------------+
|patient_ids                                          |
+-----------------------------------------------------+
|[{ABC1287651, ABC, MR, }, {EPIC2548537, EPIC, MRN, }]|
|[{5713279, , , UN}]                                  |
+-----------------------------------------------------+

root
 |-- patient_ids: array (nullable = true)
 |    |-- element: struct (containsNull = false)
 |    |    |-- id_number: string (nullable = true)
 |    |    |-- assigning_authority: string (nullable = true)
 |    |    |-- identifier_type_code: string (nullable = true)
 |    |    |-- assigning_facility: string (nullable = true)
```

In this example, the first report has two patient IDs, one from an assigning authority of ABC and one from EPIC.
The second report does not have an assigning authority for the patient ID, but instead has an assigning authority of UN.

The assigning authority and identifier type code are used to create separate columns for each patient ID type to
facilitate easier querying and analysis. For example, the `epic_mrn` column is created from the assigning authority 
"EPIC" and identifier type code "MRN". The same applies to other patient ID columns. If assigning authority and identifier
type code are not available, a column using the assigning facility is created such as `legacy_patient_id_un` for the above
example.


(diagnoses_ref)=
# Diagnoses
The `diagnoses` column contains an array of structs to represent the diagnoses.

An example of the column with schema is shown below:

```text
+---------------------------------------------------------------------------------------------+
|diagnoses                                                                                    |
+---------------------------------------------------------------------------------------------+
|[{J18.9, Pneumonia, unspecified organism, I10}, {I10, Essential (primary) hypertension, I10}]|
+---------------------------------------------------------------------------------------------+
only showing top 1 row

root
 |-- diagnoses: array (nullable = true)
 |    |-- element: struct (containsNull = false)
 |    |    |-- diagnosis_code: string (nullable = true)
 |    |    |-- diagnosis_code_text: string (nullable = true)
 |    |    |-- diagnosis_code_coding_system: string (nullable = true)
```

In this example, the report has two diagnosis codes, both of which are ICD-10 codes. The first code
is J18.9 and the second is I10. An example spark query to filter an existing dataframe `df` for reports
containing an ICD-10 diagnosis code of "J18.9" could look like:
```python
from pyspark.sql import functions as F

df.select("diagnoses").filter(
    F.exists("diagnoses", lambda x: (x.diagnosis_code == "J18.9") & (x.diagnosis_code_coding_system == "I10"))
)
```

(report_text_ref)=
# Report Text

The full report text is reconstructed by the following process:
1. Take all of the `OBX` segments in the HL7 message in order.
2. For any of the `OBX` segments with an `OBX-2` value of `TX`, replace within `OBX-5` the default repetition character `~` with newlines.
3. Join the ordered `OBX-5` values with newlines.

This full text blob is what Scout stores in the column `report_text`.

To create a rudimentarily "parsed" version of the report text, we can infer some structure to the reports from the value of the observation ID suffix defined in section 7.2.4 of the HL7 (v2.7) standard.
We also allow a nonstandard `ADN` suffix to match what our 2.3 reports seem to use instead. For this process, we:
1. Filter the `OBX` segments to retain those with a non-empty `OBX-3.1.2` value corresponding to this suffix.
2. Maintaining the order of the filtered segments, group the segments by suffix.
3. For each suffix group, join the segments by newlines and store them in the following columns:

| Observation ID Suffix | Data Lake Column Name            |
|-----------------------|----------------------------------|
| `ADT` or `ADN`        | `report_section_addendum`        |
| `GDT`                 | `report_section_findings`        |
| `IMP`                 | `report_section_impression`      |
| `TCM`                 | `report_section_technician_note` |
