# Data

The Rad Report Explorer is backed by [Delta Lake](https://delta.io/) and [MinIO](https://min.io/) to store and manage 
HL7 reports and other data and metadata. There is one main table that contains the HL7 report data. Below is a 
description of the report table schema and the mapping of HL7 fields to the report table columns. Note that some fields 
are derived from others, and some fields may not be directly mapped to HL7 fields.

| Column Name                        | HL7 Report Field | Data Type      | Nullable | Description/Notes                                                                                                                    |
|------------------------------------|------------------|----------------|----------|--------------------------------------------------------------------------------------------------------------------------------------|
| `source_file`                      |                  | string         | No       | The location of the report file.                                                                                                     |
| `updated`                          |                  | timestamp      | No       | Timestamp of the last update to the report in the Delta Lake.                                                                        |
| `message_control_id`               | MSH-10           | string         | Yes      | Unique identifier for the HL7 message.                                                                                               |
| `sending_facility`                 | MSH-4            | string         | Yes      | Facility that sent the HL7 message.                                                                                                  |
| `version_id`                       | MSH-12           | string         | Yes      | HL7 version used in the message.                                                                                                     |
| `message_dt`                       | MSH-7            | timestamp      | Yes      | Date and time the message was created.                                                                                               |
| `birth_date`                       | PID-7            | date           | Yes      | Patient’s date of birth.                                                                                                             |
| `sex`                              | PID-8            | string         | Yes      | Patient’s gender.                                                                                                                    |
| `race`                             | PID-10           | string         | Yes      | Patient’s race.                                                                                                                      |
| `zip_or_postal_code`               | PID-11.5         | string         | Yes      | Patient’s ZIP or postal code.                                                                                                        |
| `country`                          | PID-11.6         | string         | Yes      | Patient’s country.                                                                                                                   |
| `ethnic_group`                     | PID-22           | string         | Yes      | Patient’s ethnicity.                                                                                                                 |
| `patient_id_json`                  |                  | string         | Yes      | JSON representation of all patient identifiers. Patient ID columns are also created for each assigning authority (e.g., `epic_mrn`). |
| `orc_2_placer_order_number`        | ORC-2            | string         | Yes      | Placer order number from the order control segment.                                                                                  |
| `obr_2_placer_order_number`        | OBR-2            | string         | Yes      | Placer order number from the observation request segment.                                                                            |
| `orc_3_filler_order_number`        | ORC-3            | string         | Yes      | Filler order number from the order control segment.                                                                                  |
| `obr_3_filler_order_number`        | OBR-3            | string         | Yes      | Filler order number from the observation request segment.                                                                            |
| `service_identifier`               | OBR-4.1          | string         | Yes      | Code for the service or exam.                                                                                                        |
| `service_name`                     | OBR-4.2          | string         | Yes      | Name of the service or exam.                                                                                                         |
| `service_coding_system`            | OBR-4.3          | string         | Yes      | Coding system used for the service identifier.                                                                                       |
| `diagnostic_service_id`            | OBR-24           | string         | Yes      | Identifier for the diagnostic service.                                                                                               |
| `modality`                         | Derived          | string         | Yes      | Modality of the exam (e.g., CT, MRI).                                                                                                |
| `requested_dt`                     | OBR-6            | timestamp      | Yes      | Date and time the service was requested.                                                                                             |
| `observation_dt`                   | OBR-7            | timestamp      | Yes      | Date and time the observation was made.                                                                                              |
| `observation_end_dt`               | OBR-8            | timestamp      | Yes      | Date and time the observation ended.                                                                                                 |
| `results_report_status_change_dt`  | OBR-22           | timestamp      | Yes      | Date and time the report status changed.                                                                                             |
| `diagnosis_code`                   | DG1-3.1          | string         | Yes      | Diagnosis code.                                                                                                                      |
| `diagnosis_code_text`              | DG1-3.2          | string         | Yes      | Description of the diagnosis.                                                                                                        |
| `diagnosis_code_coding_system`     | DG1-3.3          | string         | Yes      | Coding system used for the diagnosis code.                                                                                           |
| `study_instance_uid`               | ZDS-1            | string         | Yes      | Unique identifier for the study instance.                                                                                            |
| `report_text`                      | OBX-5            | string         | Yes      | Full text of the diagnostic report.                                                                                                  |
| `report_status`                    | OBX-11           | string         | Yes      | Status of the diagnostic report.                                                                                                     |
| `year`                             | Derived          | integer        | Yes      | Year the message was created, derived from `message_dt`.                                                                             |

