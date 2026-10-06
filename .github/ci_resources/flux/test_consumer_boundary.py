"""Offline checks for producer/consumer trust boundaries and receipt archive failures."""

import ast
import importlib.util
import json
from pathlib import Path
import re
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


def evaluate(expression, context):
    """Interpret only the gate syntax exercised by these event fixtures.

    Parse into data, then handle an explicit node allowlist; workflow text is
    never executed as Python. Unsupported syntax fails the test rather than
    silently broadening this deliberately small GitHub-expression subset.
    """
    expression = expression.replace("&&", " and ").replace("||", " or ")
    expression = re.sub(r"!(?!=)", " not ", expression)

    def visit(node):
        if isinstance(node, ast.Constant) and type(node.value) in (str, bool, int):
            return node.value
        if isinstance(node, ast.Name) and node.id in {"github", "needs", "inputs"}:
            return context.get(node.id, {})
        if isinstance(node, ast.Attribute):
            parent = visit(node.value)
            return parent.get(node.attr) if isinstance(parent, dict) else None
        if isinstance(node, ast.BoolOp) and isinstance(node.op, (ast.And, ast.Or)):
            for child in node.values:
                value = visit(child)
                if isinstance(node.op, ast.And) and not value:
                    return value
                if isinstance(node.op, ast.Or) and value:
                    return value
            return value
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return not visit(node.operand)
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            left, right = visit(node.left), visit(node.comparators[0])
            if isinstance(node.ops[0], ast.Eq):
                return left == right
            if isinstance(node.ops[0], ast.NotEq):
                return left != right
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and not node.keywords
        ):
            if node.func.id == "always" and not node.args:
                return True
            if node.func.id == "cancelled" and not node.args:
                return context.get("cancelled", False)
            if node.func.id == "fromJSON" and len(node.args) == 1:
                return json.loads(visit(node.args[0]))
        raise AssertionError("unsupported workflow gate syntax: " + type(node).__name__)

    return visit(ast.parse(" ".join(expression.split()), mode="eval").body)


class GateSyntaxTests(unittest.TestCase):
    def test_rejects_python_execution_and_unsupported_operations(self):
        for expression in (
            "__import__('os').getcwd()",
            "github.clear()",
            "[value for value in github]",
            "github['repository']",
            "1 + 2",
            "fromJSON(value='[]')",
        ):
            with self.subTest(expression=expression), self.assertRaises(AssertionError):
                evaluate(expression, {"github": {"repository": "washu-tag/scout"}})


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
        for name in ("identity", "deploy", "published-status"):
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

    def test_no_receipt_skips_deploy_and_status_but_invalid_receipt_reports_failure(
        self,
    ):
        context = self.context()
        context["needs"]["identity"]["outputs"]["present"] = "false"
        for name in ("deploy", "published-status"):
            self.assertFalse(allows(self.workflow["jobs"][name]["if"], context))
        context["needs"]["identity"]["result"] = "failure"
        self.assertFalse(allows(self.workflow["jobs"]["deploy"]["if"], context))
        self.assertTrue(
            allows(self.workflow["jobs"]["published-status"]["if"], context)
        )

    def test_local_events_run_deploy_without_the_writer(self):
        for event in ("pull_request", "push", "workflow_dispatch"):
            context = self.context()
            context["github"].update(
                event_name=event, repository="fork/scout", event={}
            )
            context["needs"]["identity"] = {"result": "skipped", "outputs": {}}
            self.assertTrue(allows(self.workflow["jobs"]["deploy"]["if"], context))
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

    def test_both_legs_gate_published_status_and_only_manual_runs_can_select_one(self):
        deploy = self.workflow["jobs"]["deploy"]
        self.assertFalse(deploy["strategy"]["fail-fast"])
        matrix = (
            deploy["strategy"]["matrix"]["leg"].removeprefix("${{").removesuffix("}}")
        )
        for event in ("workflow_run", "pull_request", "push", "workflow_dispatch"):
            for selection in ("both", "ingest", "auth"):
                context = self.context()
                context["github"]["event_name"] = event
                context["inputs"] = {"leg": selection}
                expected = (
                    [selection]
                    if event == "workflow_dispatch" and selection != "both"
                    else ["ingest", "auth"]
                )
                self.assertEqual(evaluate(matrix, context), expected)
        writer = self.workflow["jobs"]["published-status"]
        self.assertEqual(set(writer["needs"]), {"identity", "deploy"})
        self.assertEqual(
            writer["steps"][0]["env"]["DEPLOY_RESULT"], "${{ needs.deploy.result }}"
        )

    def test_deployment_stops_on_cancellation_but_status_can_report_failure(self):
        context = self.context()
        context["cancelled"] = True
        self.assertFalse(allows(self.workflow["jobs"]["deploy"]["if"], context))
        self.assertTrue(
            allows(self.workflow["jobs"]["published-status"]["if"], context)
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
