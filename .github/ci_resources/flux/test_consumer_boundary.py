"""Offline checks for producer/consumer trust boundaries and receipt archive failures."""

import importlib.util
from pathlib import Path
import re
import stat
import tempfile
import unittest
import warnings
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
            "size_in_bytes": 400,
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

    def test_no_receipt_is_the_only_clean_skip(self):
        self.assertIsNone(self.select([{"artifacts": []}]))
        self.assertIsNone(
            self.select(
                [{"artifacts": [dict(self.artifact, name="scout-config-ref-1")]}]
            )
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
            {"size_in_bytes": receipt_archive.MAX_ARCHIVE_BYTES + 1},
            {"size_in_bytes": 0},
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
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(
                path, "w", compression=zipfile.ZIP_DEFLATED
            ) as archive:
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

    def test_extra_duplicate_traversal_and_oversize_members_fail(self):
        cases = [
            [],
            [("../scout-config-ref.json", b"{}")],
            [("scout-config-ref.json", b"{}"), ("payload.sh", b"echo bad")],
            [("scout-config-ref.json", b"{}"), ("scout-config-ref.json", b"{}")],
            [("scout-config-ref.json", b"x" * (receipt_archive.MAX_RECEIPT_BYTES + 1))],
        ]
        for case in cases:
            with self.subTest(names=[str(name) for name, _ in case]), self.assertRaises(
                ValueError
            ):
                receipt_archive.read_receipt(self.archive(case))

    def test_symlink_member_fails(self):
        member = zipfile.ZipInfo("scout-config-ref.json")
        member.create_system = 3
        member.external_attr = (stat.S_IFLNK | 0o777) << 16
        with self.assertRaises(ValueError):
            receipt_archive.read_receipt(self.archive([(member, "../../../target")]))

    def test_bad_zip_fails(self):
        path = self.archive([])
        path.write_bytes(b"not a ZIP archive")
        with self.assertRaises(zipfile.BadZipFile):
            receipt_archive.read_receipt(path)


def evaluate(expression, context):
    # Evaluate the small expression subset actually used by job gates against event fixtures.
    def resolve(match):
        value = context
        for part in match.group().split("."):
            value = value.get(part, {}) if isinstance(value, dict) else {}
        return repr(value if value != {} else None)

    expression = re.sub(
        r"(?:github|needs)(?:\.[a-zA-Z_][a-zA-Z_0-9]*)+", resolve, expression
    )
    expression = (
        expression.replace("always()", "True")
        .replace("&&", " and ")
        .replace("||", " or ")
    )
    return eval(" ".join(expression.split()), {"__builtins__": {}}, {})


def allows(expression, context):
    return bool(evaluate(expression, context))


class WorkflowBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = yaml.safe_load(
            (ROOT / ".github/workflows/deploy-flux.yaml").read_text()
        )

    def context(self):
        return {
            "github": {
                "event_name": "workflow_run",
                "repository": "washu-tag/scout",
                "event": {
                    "workflow_run": {
                        "event": "push",
                        "head_branch": "main",
                        "conclusion": "success",
                        "head_repository": {"full_name": "washu-tag/scout"},
                    }
                },
            },
            "needs": {
                "identity": {"result": "success", "outputs": {"present": "true"}}
            },
        }

    def test_each_published_job_rejects_untrusted_event_mutations(self):
        mutations = [
            ("github.repository", "attacker/scout"),
            ("github.event.workflow_run.event", "pull_request"),
            ("github.event.workflow_run.head_branch", "topic"),
            ("github.event.workflow_run.conclusion", "failure"),
            ("github.event.workflow_run.head_repository.full_name", "attacker/scout"),
        ]
        for name in ("identity", "ingest", "published-status"):
            gate = self.workflow["jobs"][name]["if"]
            self.assertTrue(allows(gate, self.context()), name)
            for path, value in mutations:
                context = self.context()
                cursor = context
                parts = path.split(".")
                for part in parts[:-1]:
                    cursor = cursor[part]
                cursor[parts[-1]] = value
                with self.subTest(job=name, mutation=path):
                    self.assertFalse(allows(gate, context))

    def test_no_receipt_skips_ingest_and_status_but_invalid_receipt_reports_failure(
        self,
    ):
        context = self.context()
        context["needs"]["identity"]["outputs"]["present"] = "false"
        for name in ("ingest", "published-status"):
            self.assertFalse(allows(self.workflow["jobs"][name]["if"], context))
        context["needs"]["identity"]["result"] = "failure"
        self.assertFalse(allows(self.workflow["jobs"]["ingest"]["if"], context))
        self.assertTrue(
            allows(self.workflow["jobs"]["published-status"]["if"], context)
        )

    def test_local_events_run_ingest_without_the_writer(self):
        for event in ("pull_request", "push", "workflow_dispatch"):
            context = self.context()
            context["github"].update(
                event_name=event, repository="fork/scout", event={}
            )
            context["needs"]["identity"] = {"result": "skipped", "outputs": {}}
            self.assertTrue(allows(self.workflow["jobs"]["ingest"]["if"], context))
            self.assertFalse(allows(self.workflow["jobs"]["identity"]["if"], context))
            self.assertFalse(
                allows(self.workflow["jobs"]["published-status"]["if"], context)
            )

    def test_manual_runs_and_published_attempts_have_independent_concurrency_groups(
        self,
    ):
        def group(event, run_id, producer_attempt=1):
            context = self.context()
            context["github"].update(
                event_name=event,
                run_id=run_id,
                workflow="Deploy via Flux (on-prem)",
                ref="refs/pull/1/merge",
            )
            if event == "workflow_run":
                context["github"]["event"]["workflow_run"].update(
                    id=10, run_attempt=producer_attempt
                )
            else:
                context["github"]["event"] = {}
            return re.sub(
                r"\$\{\{(.*?)\}\}",
                lambda m: str(evaluate(m.group(1), context)),
                self.workflow["concurrency"]["group"],
            )

        self.assertNotEqual(
            group("workflow_dispatch", 100), group("workflow_dispatch", 101)
        )
        self.assertEqual(group("pull_request", 100), group("pull_request", 101))
        self.assertNotEqual(
            group("workflow_run", 100, 1), group("workflow_run", 101, 2)
        )

    def test_writer_has_no_checkout_artifact_download_or_repository_execution(self):
        jobs = self.workflow["jobs"]
        self.assertEqual(jobs["ingest"]["permissions"], {"contents": "read"})
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
        for name in ("identity", "ingest"):
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


if __name__ == "__main__":
    unittest.main()
