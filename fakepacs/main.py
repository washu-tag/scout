import json
import logging
from functools import cache
import importlib.resources as importlib_resources
from typing import Any, Optional

import pandas as pd
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from starlette.requests import Request

DicomJson = dict[str, Any]
Study = DicomJson
Studies = list[Study]
StudyMetadata = list[DicomJson]
STUDY_INSTANCE_UID = "0020000D"
RETRIEVE_URL = "00081190"


class RadReport(BaseModel):
    accession_number: str = Field(alias="AccessionNumber")
    clinical_information: Optional[str] = Field(alias="ClinicalInformation")
    technique: Optional[str] = Field(alias="Technique")
    findings: Optional[str] = Field(alias="Findings")
    impressions: Optional[str] = Field(alias="Impressions")
    study_date: Optional[str] = Field(alias="StudyDate")
    patient_sex: Optional[str] = Field(alias="PatientSex")
    patient_age: Optional[str] = Field(alias="PatientAge")
    series_description: Optional[str] = Field(alias="SeriesDescription")
    manufacturer: Optional[str] = Field(alias="Manufacturer")
    manufacturer_model_name: Optional[str] = Field(alias="ManufacturerModelName")
    rows: Optional[int] = Field(alias="Rows")
    columns: Optional[int] = Field(alias="Columns")
    slices: Optional[int] = Field(alias="Slices")
    xy_spacing: Optional[str] = Field(alias="XYSpacing")
    z_spacing: Optional[float] = Field(alias="ZSpacing")
    rescale_slope: Optional[int] = Field(alias="RescaleSlope")
    rescale_type: Optional[str] = Field(alias="RescaleType")


logger = logging.getLogger("app")
logging.basicConfig(level=logging.INFO)


@cache
def read_json_resource(filename: str) -> DicomJson | list[DicomJson]:
    """Read resource file"""
    filepath = importlib_resources.files() / "resources" / filename
    with filepath.open() as f:
        return json.load(f)


def read_study_metadata() -> StudyMetadata:
    return read_json_resource("study_metadata.json")


def read_studies() -> Studies:
    return read_json_resource("studies.json")


@cache
def read_ctrate_reports() -> pd.DataFrame:
    # noinspection PyTypeChecker
    return pd.read_feather(
        importlib_resources.files() / "resources" / "ct_rate_reports.feather"
    )


def construct_retrieve_url(study: Study, retrieve_url_base: str) -> Study:
    study_instance_uid = study[STUDY_INSTANCE_UID]["Value"][0]
    retrieve_url = f"{retrieve_url_base}/{study_instance_uid}"
    return {
        "vr": "UR",
        "Value": [retrieve_url],
    }


def set_retrieve_urls(studies: Studies, studies_api_endpoint: str) -> Studies:
    if studies_api_endpoint.endswith("/"):
        studies_api_endpoint = studies_api_endpoint[:-1]

    for study in studies:
        study[RETRIEVE_URL] = construct_retrieve_url(study, studies_api_endpoint)
    return studies


app = FastAPI()


@app.get("/")
async def root():
    return "OK"


@app.get("/studies")
async def get_studies(request: Request) -> Studies:
    raw_study_data = read_json_resource("studies.json")
    return set_retrieve_urls(raw_study_data, str(request.url))


@app.get("/studies/{study_instance_uid}/metadata")
async def get_study_metadata(study_instance_uid: str) -> StudyMetadata:
    return read_json_resource("study_metadata.json")


@app.get("/reports/ct-rate/{accession_number}")
async def get_ctrate_report(accession_number: str) -> RadReport:
    ctrate_reports = read_ctrate_reports()
    accession_number_reports: pd.DataFrame = ctrate_reports.loc[
        ctrate_reports.AccessionNumber == accession_number
    ]
    if len(accession_number_reports) == 0:
        raise HTTPException(status_code=404, detail="Accession number not found")
    return RadReport(**accession_number_reports.iloc[0].to_dict())


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)
