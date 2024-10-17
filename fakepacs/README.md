# Fake PACS

## DICOMweb
Respond to some select DICOMweb endponts, returning static data.

```
GET /studies
```
Returns a static JSON list of study metadata.

```
GET /studies/{study_instance_uid}/metadata
```
Returns a static JSON list of a single study's instance metadata.
The `study_instance_uid` param is ignored.

## Rad Reports

```
GET /reports/ct-rate/{accession_number}
```
Return radiology report text from a modified version of the 
[CT-RATE radiology reports dataset](https://huggingface.co/datasets/ibrahimhamamci/CT-RATE/blob/main/dataset/radiology_text_reports/train_reports.csv)

The "accession number" in our data is derived from the `VolumeName` column in CT-RATE.
```python
import pandas as pd
report_df = pd.read_csv("/path/to/ctrate/train_reports.csv")
meta_df = pd.read_csv("/path/to/ctrate/train_metadata.csv", dtype={"StudyDate": str})

df = report_df.merge(meta_df, on="VolumeName", validate="one_to_one")

df["AccessionNumber"] = df["VolumeName"].apply(lambda s: s.removesuffix(".nii.gz"))
report_cols_en = ["ClinicalInformation_EN", "Technique_EN", "Findings_EN", "Impressions_EN"]
report_cols = [col.removesuffix("_EN") for col in report_cols_en]
renames = dict(zip(report_cols_en, report_cols))
renames["NumberofSlices"] = "Slices"
df.rename(columns=renames, inplace=True)

cols_to_keep = [
    "AccessionNumber",
    *report_cols,
    "StudyDate",
    "PatientSex",
    "PatientAge",
    "SeriesDescription",
    "Manufacturer",
    "ManufacturerModelName",
    "Rows",
    "Columns",
    "Slices",
    "XYSpacing",
    "ZSpacing",
    "RescaleSlope",
    "RescaleType",
]
df[cols_to_keep].to_feather("resources/ct_rate_reports.feather")
```
