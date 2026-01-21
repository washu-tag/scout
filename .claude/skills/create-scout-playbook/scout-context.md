# Scout Data Schema Reference

## Reports Table: `delta.default.reports`

The main table containing HL7 radiology report data.

### Core Metadata
| Column | Type | Description |
|--------|------|-------------|
| `source_file` | string | Location of the report file |
| `updated` | timestamp | Last update in Delta Lake |
| `message_control_id` | string | Unique HL7 message identifier |
| `sending_facility` | string | Facility that sent the message |
| `version_id` | string | HL7 version |
| `message_dt` | timestamp | Message creation datetime |

### Patient Information
| Column | Type | Description |
|--------|------|-------------|
| `mpi` | string | Legacy MPI |
| `birth_date` | date | Patient date of birth |
| `sex` | string | Patient gender |
| `race` | string | Patient race |
| `zip_or_postal_code` | string | Patient ZIP code |
| `country` | string | Patient country |
| `ethnic_group` | string | Patient ethnicity |
| `patient_name` | string | Simple "FIRST LAST" format |
| `epic_mrn` | string | Epic MRN if available |

### Order Information
| Column | Type | Description |
|--------|------|-------------|
| `orc_2_placer_order_number` | string | Placer order number (ORC) |
| `obr_2_placer_order_number` | string | Placer order number (OBR) |
| `orc_3_filler_order_number` | string | Filler order number (ORC) |
| `obr_3_filler_order_number` | string | Filler order number (OBR) |

### Service Information
| Column | Type | Description |
|--------|------|-------------|
| `service_identifier` | string | Service/exam code |
| `service_name` | string | Service/exam name |
| `service_coding_system` | string | Coding system |
| `diagnostic_service_id` | string | Diagnostic service ID |
| `modality` | string | **Derived** - CT, MRI, XR, US, NM, etc. |

### Timing Fields
| Column | Type | Description |
|--------|------|-------------|
| `requested_dt` | timestamp | When service was requested |
| `observation_dt` | timestamp | When observation was made |
| `observation_end_dt` | timestamp | When observation ended |
| `results_report_status_change_dt` | timestamp | When report status changed (finalized) |
| `patient_age` | integer | **Derived** - Age at time of report |

### Personnel
| Column | Type | Description |
|--------|------|-------------|
| `ordering_provider` | string | Ordering provider name |
| `principal_result_interpreter` | string | Reading radiologist |
| `assistant_result_interpreter` | array[string] | Assistant interpreters |
| `technician` | array[string] | Technicians |

### Report Content
| Column | Type | Description |
|--------|------|-------------|
| `report_text` | string | Full report text |
| `report_status` | string | Report status |
| `study_instance_uid` | string | DICOM study UID |
| `report_section_addendum` | string | Addendum section |
| `report_section_findings` | string | Findings section |
| `report_section_impression` | string | Impression section |
| `report_section_technician_note` | string | Tech note section |

### Diagnoses
| Column | Type | Description |
|--------|------|-------------|
| `diagnoses` | array[struct] | Array of diagnosis codes |
| `diagnoses_consolidated` | string | Semicolon-delimited diagnoses |

Diagnosis struct fields:
- `diagnosis_code` - The ICD code
- `diagnosis_code_text` - Human-readable text
- `diagnosis_code_coding_system` - Usually "I10" for ICD-10

### Partitioning
| Column | Type | Description |
|--------|------|-------------|
| `year` | integer | **Derived** - Year from message_dt |

## Common Modalities

- `CT` - Computed Tomography
- `MRI` - Magnetic Resonance Imaging
- `XR` - X-Ray / Radiograph
- `US` - Ultrasound
- `NM` - Nuclear Medicine
- `MG` - Mammography
- `FL` - Fluoroscopy
- `PET` - PET Scan

## Common Query Patterns

### Volume Analysis
```sql
SELECT modality, COUNT(*) as count
FROM delta.default.reports
WHERE message_dt >= TIMESTAMP '2024-01-01'
GROUP BY modality
ORDER BY count DESC
```

### TAT Calculation
```sql
SELECT
    modality,
    AVG(CAST(DATE_DIFF('second', observation_dt, results_report_status_change_dt) AS DOUBLE) / 3600.0) as avg_tat_hours
FROM delta.default.reports
WHERE observation_dt IS NOT NULL
  AND results_report_status_change_dt IS NOT NULL
GROUP BY modality
```

### Time Series
```sql
SELECT
    DATE_TRUNC('week', message_dt) as week,
    modality,
    COUNT(*) as count
FROM delta.default.reports
WHERE message_dt >= TIMESTAMP '2024-01-01'
GROUP BY DATE_TRUNC('week', message_dt), modality
ORDER BY week
```

### Report Quality Checks
```sql
SELECT
    SUM(CASE WHEN report_section_findings IS NOT NULL THEN 1 ELSE 0 END) as has_findings,
    SUM(CASE WHEN report_section_impression IS NOT NULL THEN 1 ELSE 0 END) as has_impression,
    COUNT(*) as total
FROM delta.default.reports
```
