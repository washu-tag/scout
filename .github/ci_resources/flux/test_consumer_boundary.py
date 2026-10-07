"""Offline checks for producer/consumer trust boundaries and receipt archive failures."""

import importlib.util
from pathlib import Path
import tempfile
import unittest
import zipfile

import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
spec = importlib.util.spec_from_file_location(
    "receipt_archive", HERE / "receipt_archive.py"
)
receipt_archive = importlib.util.module_from_spec(spec)
spec.loader.exec_module(receipt_archive)


class ReceiptSelectionTests(unittest.TestCase):
    def setUp(self):
        self.artifact = {
            "name": "scout-config-ref-2",
            "id": 99,
            "expired": False,
            "workflow_run": {"id": 123, "head_sha": "a" * 40},
        }

    def select(self, artifacts):
        return receipt_archive.select_artifact(
            artifacts, run_id=123, run_attempt=2, revision="a" * 40
        )

    def test_selects_only_current_attempt_across_pages(self):
        old = dict(self.artifact, name="scout-config-ref-1", id=98)
        self.assertEqual(
            self.select([{"artifacts": [old]}, {"artifacts": [self.artifact]}]), 99
        )

    def test_docs_only_run_without_any_receipt_is_a_clean_skip(self):
        self.assertIsNone(self.select([{"artifacts": []}]))
        self.assertIsNone(
            self.select([{"artifacts": [{"name": "unrelated-build-output"}]}])
        )

    def test_partial_rerun_requires_rerunning_all_jobs(self):
        # A successful producer rerun must not silently skip proof or reuse its
        # previous attempt's receipt when the publishing job was not rerun.
        for other_attempt in (1, 3):
            with self.subTest(attempt=other_attempt), self.assertRaisesRegex(
                ValueError, "rerun all jobs"
            ):
                self.select(
                    [
                        {
                            "artifacts": [
                                dict(
                                    self.artifact,
                                    name=f"scout-config-ref-{other_attempt}",
                                )
                            ]
                        }
                    ]
                )

    def test_duplicate_receipt_fails_even_across_pages(self):
        with self.assertRaises(ValueError):
            self.select(
                [{"artifacts": [self.artifact]}, {"artifacts": [self.artifact]}]
            )

    def test_bad_metadata_never_becomes_a_skip(self):
        changes = [
            {"expired": True},
            {"expired": None},
            {"id": True},
            {"id": "99"},
            {"id": -1},
            {"workflow_run": {"id": 124, "head_sha": "a" * 40}},
            {"workflow_run": {"id": 123, "head_sha": "b" * 40}},
            {"workflow_run": {}},
        ]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.select([{"artifacts": [dict(self.artifact, **change)]}])

    def test_bad_api_response_never_becomes_a_skip(self):
        for response in ({}, [], [{"message": "Forbidden"}], [{"artifacts": [None]}]):
            with self.subTest(response=response), self.assertRaises(ValueError):
                self.select(response)


class ReceiptArchiveTests(unittest.TestCase):
    def archive(self, entries):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "receipt.zip"
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in entries:
                archive.writestr(name, data)
        return path

    def test_reads_one_expected_member_without_extracting_paths(self):
        self.assertEqual(
            receipt_archive.read_receipt(
                self.archive([("scout-config-ref.json", b"{}\n")])
            ),
            b"{}\n",
        )

    def test_missing_exact_receipt_member_fails(self):
        for entries in ([], [("elsewhere/scout-config-ref.json", b"{}")]):
            with self.subTest(entries=entries), self.assertRaises(ValueError):
                receipt_archive.read_receipt(self.archive(entries))

    def test_bad_zip_fails(self):
        path = self.archive([])
        path.write_bytes(b"not a ZIP archive")
        with self.assertRaises(zipfile.BadZipFile):
            receipt_archive.read_receipt(path)


class WorkflowBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = yaml.safe_load(
            (ROOT / ".github/workflows/deploy-flux.yaml").read_text()
        )

    def test_both_legs_gate_published_status(self):
        deploy = self.workflow["jobs"]["deploy"]
        self.assertFalse(deploy["strategy"]["fail-fast"])
        writer = self.workflow["jobs"]["published-status"]
        self.assertEqual(set(writer["needs"]), {"identity", "deploy"})
        self.assertEqual(
            writer["steps"][0]["env"]["DEPLOY_RESULT"], "${{ needs.deploy.result }}"
        )

    def test_writer_has_no_checkout_artifact_download_or_repository_execution(self):
        jobs = self.workflow["jobs"]
        self.assertEqual(jobs["deploy"]["permissions"], {"contents": "read"})
        self.assertEqual(
            jobs["identity"]["permissions"], {"contents": "read", "actions": "read"}
        )
        writer = jobs["published-status"]
        self.assertEqual(writer["permissions"], {"statuses": "write"})
        self.assertEqual(len(writer["steps"]), 1)
        script = writer["steps"][0]
        self.assertNotIn("uses", script)
        self.assertNotRegex(
            script["run"], r"checkout|download|python|source |bash |sh |\./"
        )
        self.assertIn("/repos/washu-tag/scout/statuses/${PRODUCER_SHA}", script["run"])

    def test_published_checkout_and_flux_source_keep_exact_identity(self):
        for name in ("identity", "deploy"):
            checkout = self.workflow["jobs"][name]["steps"][0]
            self.assertIn("github.event.workflow_run.head_sha", checkout["with"]["ref"])
            self.assertFalse(checkout["with"]["persist-credentials"])
        source = (HERE / "site/scout-config-source.yaml").read_text()
        config = yaml.safe_load(source)["spec"]
        self.assertEqual(config["ref"], {"digest": "@CONFIG_DIGEST@"})
        self.assertIn(
            "github.event.workflow_run.run_attempt",
            self.workflow["concurrency"]["group"],
        )
        self.assertEqual(
            self.workflow["concurrency"]["cancel-in-progress"],
            "${{ github.event_name != 'workflow_run' }}",
        )

    def test_site_has_independent_trust_and_verification_gates_deploy(self):
        roots = list(yaml.safe_load_all((HERE / "roots-site.yaml").read_text()))
        site = next(d["spec"] for d in roots if d["kind"] == "OCIRepository")
        config = yaml.safe_load((HERE / "site/scout-config-source.yaml").read_text())[
            "spec"
        ]
        self.assertEqual(site["ref"], {"digest": "@SITE_DIGEST@"})
        self.assertEqual(
            site["verify"],
            {
                "provider": "cosign",
                "secretRef": {"name": "scout-site-cosign-pub"},
            },
        )
        self.assertEqual(config["verify"]["secretRef"]["name"], "scout-cosign-pub")
        resources = yaml.safe_load((HERE / "site/kustomization.yaml").read_text())[
            "resources"
        ]
        self.assertEqual(
            set(resources),
            {
                "cluster-vars.yaml",
                "scout-secret-values.yaml",
                "scout-config-source.yaml",
            },
        )
        steps = self.workflow["jobs"]["deploy"]["steps"]
        verify_index = next(
            i
            for i, s in enumerate(steps)
            if s.get("name") == "Reconcile the verified site and config sources"
        )
        ingest_index = next(
            i for i, s in enumerate(steps) if s.get("id") == "reconcile"
        )
        verify = steps[verify_index]
        self.assertLess(verify_index, ingest_index)
        self.assertFalse(verify.get("continue-on-error", False))
        self.assertNotIn("if", verify)  # Required in both published and local modes.
        run = verify["run"]
        self.assertLess(
            run.index("wait_ready.py scout-site-source"),
            run.index("wait --for=condition=SourceVerified"),
        )
        self.assertIn("ocirepository/scout-site ocirepository/scout-config", run)
        self.assertIn(".status.artifact.revision == $digest", run)
        self.assertIn(
            'check_source scout-site "$SITE_SOURCE" "$SITE_DIGEST" scout-site-cosign-pub',
            run,
        )
        self.assertIn(
            'check_source scout-config "$CONFIG_SOURCE" "$CONFIG_DIGEST" scout-cosign-pub',
            run,
        )


if __name__ == "__main__":
    unittest.main()
