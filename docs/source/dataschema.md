# Data Schema

Scout is backed by [Delta Lake](https://delta.io/) and [MinIO](https://min.io/) to store 
and manage HL7 radiology reports and other data and metadata. There is one main table that contains the report data,
along with several downstream tables that contain results derived from the primary table. These additional tables
are documented in {ref}`Downstream Tables <downstream_tables_ref>`. Below is a description of the report table schema
and the mapping of HL7 fields to the report table columns. Note that some fields are derived from others, and some fields
may not be directly mapped to HL7 fields.

(schema_table_ref)=
| Column Name                         | HL7 Report Field | Data Type       | Nullable | Description/Notes                                                                                                                                                                                                          |
|-------------------------------------|------------------|-----------------|----------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `source_file`                       |                  | string          | No       | The location of the report file.                                                                                                                                                                                           |
| `updated`                           |                  | timestamp       | No       | Timestamp of the last update to the report in the Delta Lake.                                                                                                                                                              |
| `message_control_id`                | MSH-10           | string          | Yes      | Unique identifier for the HL7 message.                                                                                                                                                                                     |
| `sending_facility`                  | MSH-4            | string          | Yes      | Facility that sent the HL7 message.                                                                                                                                                                                        |
| `version_id`                        | MSH-12           | string          | Yes      | HL7 version used in the message.                                                                                                                                                                                           |
| `message_dt`                        | MSH-7            | timestamp       | Yes      | Date and time the message was created.                                                                                                                                                                                     |
| `mpi`                               | PID-2            | string          | Yes      | Legacy MPI for patient.                                                                                                                                                                                                    |
| `birth_date`                        | PID-7            | date            | Yes      | Patient’s date of birth.                                                                                                                                                                                                   |
| `sex`                               | PID-8            | string          | Yes      | Patient’s gender.                                                                                                                                                                                                          |
| `race`                              | PID-10           | string          | Yes      | Patient’s race.                                                                                                                                                                                                            |
| `zip_or_postal_code`                | PID-11.5         | string          | Yes      | Patient’s ZIP or postal code.                                                                                                                                                                                              |
| `country`                           | PID-11.6         | string          | Yes      | Patient’s country.                                                                                                                                                                                                         |
| `ethnic_group`                      | PID-22           | string          | Yes      | Patient’s ethnicity.                                                                                                                                                                                                       |
| `patient_ids`                       | PID-3            | array of struct | Yes      | Structured representation of all patient identifiers. Patient ID columns are also created for each assigning authority (e.g., `epic_mrn`) or assigning facility. See {ref}`Patient IDs <patient_ids_ref>` for more detail. |
| `full_patient_name`                 | PID-5            | array of struct | Yes      | Structured representation of all available forms of the patient's name. See {ref}`Patient Name <patient_name_ref>` for more detail.                                                                                        |
| `patient_name`                      | PID-5            | string          | Yes      | Simple "FIRST LAST" representation of the patient's name. See {ref}`Patient Name <patient_name_ref>` for more detail.                                                                                                      |
| `epic_mrn`                          |                  | string          | Yes      | Patient ID from Epic system.                                                                                                                                                                                               |
| `orc_2_placer_order_number`         | ORC-2            | string          | Yes      | Placer order number from the order control segment.                                                                                                                                                                        |
| `obr_2_placer_order_number`         | OBR-2            | string          | Yes      | Placer order number from the observation request segment.                                                                                                                                                                  |
| `orc_3_filler_order_number`         | ORC-3            | string          | Yes      | Filler order number from the order control segment.                                                                                                                                                                        |
| `obr_3_filler_order_number`         | OBR-3            | string          | Yes      | Filler order number from the observation request segment.                                                                                                                                                                  |
| `service_identifier`                | OBR-4.1          | string          | Yes      | Code for the service or exam.                                                                                                                                                                                              |
| `service_name`                      | OBR-4.2          | string          | Yes      | Name of the service or exam.                                                                                                                                                                                               |
| `service_coding_system`             | OBR-4.3          | string          | Yes      | Coding system used for the service identifier.                                                                                                                                                                             |
| `diagnostic_service_id`             | OBR-24           | string          | Yes      | Identifier for the diagnostic service.                                                                                                                                                                                     |
| `modality`                          | Derived          | string          | Yes      | Modality of the exam (e.g., CT, MRI).                                                                                                                                                                                      |
| `requested_dt`                      | OBR-6            | timestamp       | Yes      | Date and time the service was requested.                                                                                                                                                                                   |
| `patient_age`                       | Derived          | integer         | Yes      | Patient age at time of report as calculated between `birth_date` and `requested_dt`.                                                                                                                                       |
| `observation_dt`                    | OBR-7            | timestamp       | Yes      | Date and time the observation was made.                                                                                                                                                                                    |
| `observation_end_dt`                | OBR-8            | timestamp       | Yes      | Date and time the observation ended.                                                                                                                                                                                       |
| `full_ordering_provider`            | OBR-16           | array of struct | Yes      | Array of name + ID representations available in ordering provider field. See {ref}`Name + IDs <name_and_ids_ref>` for more detail.                                                                                         |
| `ordering_provider`                 | OBR-16           | string          | Yes      | Name of the ordering provider in format "FIRST LAST". See {ref}`Name + IDs <name_and_ids_ref>` for more detail.                                                                                                            |
| `results_report_status_change_dt`   | OBR-22           | timestamp       | Yes      | Date and time the report status changed.                                                                                                                                                                                   |
| `full_principal_result_interpreter` | OBR-32           | array of struct | Yes      | Array of name + ID representations available in principal result interpreter field. See {ref}`Name + IDs <name_and_ids_ref>` for more detail.                                                                              |
| `principal_result_interpreter`      | OBR-32           | string          | Yes      | Name within principal result interpreter field in format "FIRST LAST". See {ref}`Name + IDs <name_and_ids_ref>` for more detail.                                                                                           |
| `full_assistant_result_interpreter` | OBR-33           | array of struct | Yes      | Array of name + ID representations available in assistant result interpreter field. See {ref}`Name + IDs <name_and_ids_ref>` for more detail.                                                                              |
| `assistant_result_interpreter`      | OBR-33           | array of string | Yes      | Array of names within assistant result interpreter field in format "FIRST LAST". Duplicates and empty names are filtered out. See {ref}`Name + IDs <name_and_ids_ref>` for more detail.                                    |
| `full_technician`                   | OBR-34           | array of struct | Yes      | Array of name + ID representations available in technician field. See {ref}`Name + IDs <name_and_ids_ref>` for more detail.                                                                                                |
| `technician`                        | OBR-34           | array of string | Yes      | Array of names within technician field in format "FIRST LAST". Duplicates and empty names are filtered out. See {ref}`Name + IDs <name_and_ids_ref>` for more detail.                                                      |
| `diagnoses`                         | DG1-3            | array of struct | Yes      | Diagnosis codes for the report. See {ref}`Diagnoses <diagnoses_ref>` for more detail.                                                                                                                                      |
| `diagnoses_consolidated`            | DG1-3            | string          | Yes      | Semi-colon delimited list of code meanings from `diagnoses` column. See {ref}`Diagnoses <diagnoses_ref>` for more detail.                                                                                                  |
| `study_instance_uid`                | ZDS-1            | string          | Yes      | Unique identifier for the study instance.                                                                                                                                                                                  |
| `report_text`                       | OBX-5            | string          | Yes      | Full text of the diagnostic report. See {ref}`Report Text <report_text_ref>` for more detail.                                                                                                                              |
| `report_section_addendum`           | OBX-5            | string          | Yes      | Inferred addendum section of the report text. See {ref}`Report Text <report_text_ref>` for more detail.                                                                                                                    |
| `report_section_findings`           | OBX-5            | string          | Yes      | Inferred findings section of the report text. See {ref}`Report Text <report_text_ref>` for more detail.                                                                                                                    |
| `report_section_impression`         | OBX-5            | string          | Yes      | Inferred impression section of the report text. See {ref}`Report Text <report_text_ref>` for more detail.                                                                                                                  |
| `report_section_technician_note`    | OBX-5            | string          | Yes      | Inferred technician note section of the report text. See {ref}`Report Text <report_text_ref>` for more detail.                                                                                                             |
| `report_status`                     | OBX-11           | string          | Yes      | Status of the diagnostic report.                                                                                                                                                                                           |
| `year`                              | Derived          | integer         | Yes      | Year the message was created, derived from `message_dt`.                                                                                                                                                                   |


(patient_name_ref)=
## Patient Name
The name of the patient is represented in two different ways: an array of structs and a simple string. The intention
behind this decision is that users wanting access to the more complex fields in a name (or the multiple representations
of a name that HL7 supports) have access to this information in `full_patient_name`, while users needing only a simple
name representation have that at their fingertips in `patient_name`.

An example of these columns with schema is shown below:

```text
+-----------------------------------------------------+------------+
|full_patient_name                                    |patient_name|
+-----------------------------------------------------+------------+
|[{DAVIS, KELLY, MELISSA, NULL, Lt. Col., NULL, NULL}]|KELLY DAVIS |
+-----------------------------------------------------+------------+

root
 |-- full_patient_name: array (nullable = true)
 |    |-- element: struct (containsNull = true)
 |    |    |-- family_name: string (nullable = true)
 |    |    |-- given_name: string (nullable = true)
 |    |    |-- second_and_further_names: string (nullable = true)
 |    |    |-- suffix: string (nullable = true)
 |    |    |-- prefix: string (nullable = true)
 |    |    |-- degree: string (nullable = true)
 |    |    |-- name_type_code: string (nullable = true)
 |-- patient_name: string (nullable = true)
```


(name_and_ids_ref)=
## Name + IDs
Several name fields have columns with a "full" and simplified representation (such as `full_ordering_provider`
and `ordering_provider`). The intention behind this decision is that users wanting access to the more complex fields in
a name or IDs associated to a person in repetitions of the field have access to this information the "full" column,
while users needing only simple name representations have that at their fingertips in the simplified column.

An example of a pair of these columns with schema is shown below:
```text
root
 |-- full_ordering_provider: array (nullable = true)
 |    |-- element: struct (containsNull = true)
 |    |    |-- id_number: string (nullable = true)
 |    |    |-- family_name: string (nullable = true)
 |    |    |-- given_name: string (nullable = true)
 |    |    |-- second_and_further_names: string (nullable = true)
 |    |    |-- suffix: string (nullable = true)
 |    |    |-- prefix: string (nullable = true)
 |    |    |-- degree: string (nullable = true)
 |    |    |-- name_type_code: string (nullable = true)
 |    |    |-- assigning_authority: string (nullable = true)
 |    |    |-- identifier_type_code: string (nullable = true)
 |    |    |-- assigning_facility: string (nullable = true)
 |-- ordering_provider: string (nullable = true)
 
+----------------------------------------------------------------------------------------------------------------------------------------------------+-----------------+
|full_ordering_provider                                                                                                                              |ordering_provider|
+----------------------------------------------------------------------------------------------------------------------------------------------------+-----------------+
|[{D4369466, GEORGE, MARIA, AMBER, NULL, NULL, M.D., NULL, ABC, NULL, NULL}, {E55390123, GEORGE, MARIA, A., NULL, NULL, M.D., NULL, ABC, NULL, NULL}]|MARIA GEORGE     |
+----------------------------------------------------------------------------------------------------------------------------------------------------+-----------------+
```


(patient_ids_ref)=
## Patient IDs
The `patient_ids` column contains an array of structs to represent all patient identifiers associated with the report.

An example of the column with schema is shown below:

```text
+-------------------------------------------------------------+
|patient_ids                                                  |
+-------------------------------------------------------------+
|[{ABC1287651, ABC, MR, NULL}, {EPIC2548537, EPIC, MRN, NULL}]|
|[{5713279, NULL, NULL, UN}]                                  |
+-------------------------------------------------------------+

root
 |-- patient_ids: array (nullable = true)
 |    |-- element: struct (containsNull = false)
 |    |    |-- id_number: string (nullable = true)
 |    |    |-- assigning_authority: string (nullable = true)
 |    |    |-- identifier_type_code: string (nullable = true)
 |    |    |-- assigning_facility: string (nullable = true)
```

In this example, the first report has two patient IDs, one from an assigning authority of ABC and one from EPIC.
The second report does not have an assigning authority for the patient ID, but instead has an assigning facility of UN.

The assigning authority and identifier type code are used to create separate columns for each patient ID type to
facilitate easier querying and analysis. For example, the `epic_mrn` column is created from the assigning authority 
"EPIC" and identifier type code "MRN". The same applies to other patient ID columns. If assigning authority and identifier
type code are not available, a column using the assigning facility is created such as `legacy_patient_id_un` for the above
example.


(diagnoses_ref)=
## Diagnoses
The `diagnoses` column contains an array of structs to represent the diagnoses.

An example of the column with schema is shown below:

```text
+------------------------------------------------------------------------------------------+-----------------------------------------------------------+
|diagnoses                                                                                 |diagnoses_consolidated                                     |
+------------------------------------------------------------------------------------------+-----------------------------------------------------------+
|[{I48.91, Unspecified atrial fibrillation, I10}, {I50.9, Heart failure, unspecified, I10}]|Unspecified atrial fibrillation; Heart failure, unspecified|
+------------------------------------------------------------------------------------------+-----------------------------------------------------------+

root
 |-- diagnoses: array (nullable = true)
 |    |-- element: struct (containsNull = false)
 |    |    |-- diagnosis_code: string (nullable = true)
 |    |    |-- diagnosis_code_text: string (nullable = true)
 |    |    |-- diagnosis_code_coding_system: string (nullable = true)
 |-- diagnoses_consolidated: string (nullable = true)
```

In this example, the report has two diagnosis codes, both of which are ICD-10 codes. The first code
is I48.91 and the second is I50.9. An example spark query to filter an existing dataframe `df` for reports
containing an ICD-10 diagnosis code of "I48.91" could look like:
```python
from pyspark.sql import functions as F

df.select("diagnoses").filter(
    F.exists("diagnoses", lambda x: (x.diagnosis_code == "I48.91") & (x.diagnosis_code_coding_system == "I10"))
)
```

The `diagnoses_consolidated` column is provided as a semicolon-delimited string derived from the
list of diagnosis code text values. It may be more easily usable for simple text searches.


(report_text_ref)=
## Report Text

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

(downstream_tables_ref)=
# Downstream Tables

In addition to the primary reports table, Scout contains several derived tables to provide additional information.

(curated_table_ref)=
## reports_curated

The "curated" reports table contains a few changes targeted at smoothing out some of the WashU-specific eccentricities
in the base report table. A row in the curated table looks exactly the same as a row in the {ref}`base reports table <schema_table_ref>`
(and there is a 1-1 mapping between them), except for the following changes:

| Base report table Column                                  | Curated report table Column                                                                                                                                                                                                                                                             |
|-----------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `source_file`                                             | Renamed to `primary_report_identifier`                                                                                                                                                                                                                                                  |
| `orc_2_placer_order_number` & `obr_2_placer_order_number` | Replaced with `placer_order_number`, containing the first non-empty value from the source columns                                                                                                                                                                                       |
| `orc_3_filler_order_number` & `obr_3_filler_order_number` | Replaced with `accession_number` and `primary_study_identifier`, containing the first non-empty value from the source columns. For our data, `accession_number` and `primary_study_identifier` will be duplicates, but they exist as separate columns for sites where that is not true. |
| `patient_ids`                                             | See note below about `primary_patient_identifier`                                                                                                                                                                                                                                       |

In practice, the Patient IDs available to Scout in the reports are rather messy and have some consistency problems. In the curated
table, Scout persists the {ref}`patient_ids <patient_ids_ref>` column as-is, but it also derives a patient ID to store in
`primary_patient_identifier`. Due to limitations in the data available to Scout, this will not allow a user to perform longitudinal
queries that can track an individual patient over all HL7 versions in the delta lake. Rather, it should provide a single column
that will hopefully be consistent for reports in a given HL7 version. In other words, two values of `primary_patient_identifier` may
actually correspond to the same patient as Scout does not have the information to disambiguate in all cases.

(latest_table_ref)=
## reports_latest

In practice, the report messages contain several versions of a single report, which are all captured as individual rows
in the base report table and {ref}`curated table <curated_table_ref>`. For many purposes, it is significantly easier to
work with only the canonical "latest" version of each report without needing to worry about deduplication. The "latest" table
provides that as the subset of the curated table where only the most recent (defined by `message_dt`) report is kept.

_Warning_: while working with data in the latest table, it is important to understand the consequences. While most of the reports
that have been dropped in moving from the curated table to the latest table are earlier copies of a report missing some
data added later, there _are_ some types of studies where the study is read in parts such that analysis of the study is broken
up into two distinct reports. In these scenarios, one of the distinct reports will be treated as a preliminary copy
and therefore not be included in the latest table.

## reports_dx
... explain column chnges id col, dx exp
... 