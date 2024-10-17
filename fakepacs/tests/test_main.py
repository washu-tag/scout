import random
import string

from fastapi.testclient import TestClient

from main import app, read_ctrate_reports, read_studies, read_study_metadata


class Test:
    client = TestClient(app)

    def test_get_studies(self):
        """/studies returns static result from studies.json"""
        response = self.client.get("/studies")

        assert response.status_code == 200
        assert response.json() == read_studies()

    def test_get_study_metadata(self):
        """/studies/{anything}/metadata returns static result from study_metadata.json"""
        study_instance_uid = "".join(
            random.choice(string.ascii_uppercase) for _ in range(10)
        )
        response = self.client.get(f"/studies/{study_instance_uid}/metadata")

        assert response.status_code == 200
        assert response.json() == read_study_metadata()

    def test_get_ctrate_report(self):
        """/reports/ct-rate/{accession_number} returns value from ct_rate_train_reports"""
        ctrate_reports = read_ctrate_reports()
        sample_report = ctrate_reports.sample(1).iloc[0]
        accession_number = sample_report["AccessionNumber"]

        response = self.client.get(f"/reports/ct-rate/{accession_number}")

        assert response.status_code == 200
        assert response.json() == sample_report.to_dict()

    def test_get_ctrate_report_not_found(self):
        """/reports/ct-rate/{accession_number} with accession number not found"""
        accession_number = "".join(
            random.choice(string.ascii_uppercase) for _ in range(10)
        )
        response = self.client.get(f"/reports/ct-rate/{accession_number}")

        assert response.status_code == 404
