import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import yaml


ANSIBLE = Path(__file__).resolve().parents[2]
PLAYBOOK = shutil.which("ansible-playbook")
FIXTURE_ACTION = """
import json
from ansible.plugins.action import ActionBase

class ActionModule(ActionBase):
    def run(self, tmp=None, task_vars=None):
        action = self._task.args['action']
        args = self._task.args['args']
        with open(task_vars['fixture_trace'], 'a') as stream:
            stream.write(json.dumps({
                'action': action, 'args': args,
                'delegate_to': self._task.delegate_to,
            }) + '\\n')
        result = {'changed': False}
        if action == 'shell':
            result.update(rc=task_vars['fixture_installed_rc']
                          if 'rpm -q' in args['_raw_params'] else 0)
        elif action == 'stat':
            result['stat'] = {'exists': task_vars['fixture_key_exists']}
        elif action == 'uri':
            result['content'] = 'synthetic-public-key'
        elif action == 'rpm_key' and task_vars['fixture_bad_key']:
            result.update(failed=True, msg='Fingerprint mismatch')
        return result
"""


@unittest.skipUnless(PLAYBOOK, "ansible-playbook is required")
class PythonKubernetesPackagesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        (cls.root / "playbooks").mkdir()
        (cls.root / "action_plugins").mkdir()
        (cls.root / "action_plugins" / "package_fixture.py").write_text(FIXTURE_ACTION)
        for role in ("scout_common", "python-kubernetes"):
            shutil.copytree(ANSIBLE / "roles" / role, cls.root / "roles" / role)
        intercepted = {"shell", "package", "stat", "uri", "copy", "rpm_key", "template"}
        for path in (cls.root / "roles").glob("*/tasks/*.yaml"):
            tasks = yaml.safe_load(path.read_text())
            if not tasks:
                continue
            for task in tasks:
                for action in intercepted:
                    key = "ansible.builtin." + action
                    if key in task:
                        args = task.pop(key)
                        task["package_fixture"] = {
                            "action": action,
                            "args": (
                                args
                                if isinstance(args, dict)
                                else {"_raw_params": args}
                            ),
                        }
            path.write_text(yaml.safe_dump(tasks, sort_keys=False))

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def run_role(
        self,
        *,
        installed=False,
        air_gapped=False,
        mode="none",
        distribution="Rocky",
        staging=True,
        key_exists=False,
        bad_key=False,
        proxy_url=None
    ):
        trace = self.root / "trace.jsonl"
        trace.write_text("")
        variables = {
            "ansible_connection": "local",
            "ansible_os_family": "RedHat",
            "ansible_distribution": distribution,
            "ansible_distribution_major_version": "8",
            "air_gapped": air_gapped,
            "package_proxy_mode": mode,
            "yum_proxy_base_url": (
                proxy_url
                if proxy_url is not None
                else (
                    "https://packages.example.test/repository" if mode != "none" else ""
                )
            ),
            "fixture_trace": str(trace),
            "fixture_installed_rc": 0 if installed else 1,
            "fixture_key_exists": key_exists,
            "fixture_bad_key": bad_key,
        }
        inventory = {"all": {"vars": variables, "hosts": {"client": {}}}}
        if staging:
            staging_host = staging if isinstance(staging, str) else "stage"
            inventory["all"]["children"] = {"staging": {"hosts": {staging_host: {}}}}
        inventory_path = self.root / "inventory.yaml"
        inventory_path.write_text(yaml.safe_dump(inventory))
        playbook = self.root / "playbooks" / "test.yaml"
        package_vars = yaml.safe_load(
            (ANSIBLE / "roles/python-kubernetes/tasks/main.yaml").read_text()
        )[0]["vars"]
        playbook.write_text(
            yaml.safe_dump(
                [
                    {
                        "hosts": "client",
                        "gather_facts": False,
                        "pre_tasks": [
                            {
                                "ansible.builtin.set_fact": {
                                    "ansible_facts": {"distribution": distribution}
                                }
                            }
                        ],
                        "roles": [{"role": "python-kubernetes"}],
                        "tasks": [
                            {
                                "name": "Verify the rendered proxy repository security settings",
                                "ansible.builtin.assert": {
                                    "that": [
                                        "'gpgcheck=1' in proxy_repository",
                                        "'sslverify=1' in proxy_repository",
                                        "'gpgkey=file://' ~ python_kubernetes_epel_key_path in proxy_repository",
                                        "'baseurl=' ~ yum_proxy_base_url ~ '/epel-9/' in proxy_repository",
                                    ]
                                },
                                "vars": {
                                    "yum_repo": package_vars["yum_repo"],
                                    "proxy_repository": "{{ lookup('template', '"
                                    + str(
                                        ANSIBLE
                                        / "roles/scout_common/templates/yum-repo.j2"
                                    )
                                    + "') }}",
                                },
                                "when": [
                                    "package_proxy_mode != 'none'",
                                    "packages_check.rc != 0",
                                ],
                            }
                        ],
                    }
                ]
            )
        )
        result = subprocess.run(
            [PLAYBOOK, "-i", str(inventory_path), str(playbook)],
            env={
                **os.environ,
                "ANSIBLE_ROLES_PATH": str(self.root / "roles"),
                "ANSIBLE_ACTION_PLUGINS": str(self.root / "action_plugins"),
                "ANSIBLE_NOCOLOR": "1",
            },
            capture_output=True,
            text=True,
            timeout=40,
        )
        events = [json.loads(line) for line in trace.read_text().splitlines()]
        return result, events

    def assert_success(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_installed_offline_packages_leave_repositories_untouched(self):
        for mode, distribution in (
            ("none", "Rocky"),
            ("external", "Fedora"),
            ("nexus", "Amazon"),
        ):
            with self.subTest(mode=mode, distribution=distribution):
                result, events = self.run_role(
                    installed=True,
                    air_gapped=True,
                    mode=mode,
                    distribution=distribution,
                )
                self.assert_success(result)
                self.assertEqual([e["action"] for e in events], ["shell", "shell"])

    def test_missing_offline_packages_fail_before_repository_changes(self):
        for mode, proxy_url in (
            ("none", ""),
            ("none", "https://unused.test"),
            ("external", ""),
        ):
            with self.subTest(mode=mode, proxy_url=proxy_url):
                result, events = self.run_role(
                    air_gapped=True,
                    mode=mode,
                    proxy_url=proxy_url,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(
                    "Air-gapped RPM installation requires a package proxy",
                    result.stdout,
                )
                self.assertEqual([e["action"] for e in events], ["shell"])

    def test_connected_rocky_uses_native_epel_release(self):
        result, events = self.run_role()
        self.assert_success(result)
        self.assertEqual(
            [e["args"]["name"] for e in events if e["action"] == "package"],
            ["epel-release", ["python3-kubernetes"]],
        )
        self.assertFalse(
            any(e["action"] in ("uri", "template", "rpm_key") for e in events)
        )

    def test_other_connected_distributions_keep_native_package_sources(self):
        for distribution in ("Fedora", "Amazon", "RedHat"):
            with self.subTest(distribution=distribution):
                result, events = self.run_role(distribution=distribution)
                self.assert_success(result)
                self.assertEqual(
                    [e["args"]["name"] for e in events if e["action"] == "package"],
                    [["python3-kubernetes"]],
                )

    def test_proxy_key_download_is_delegated_and_authenticated_before_repo(self):
        for mode, staging, delegate in (
            ("nexus", True, "stage"),
            ("nexus", "client", "client"),
            ("external", False, "localhost"),
        ):
            with self.subTest(mode=mode):
                result, events = self.run_role(
                    air_gapped=True, mode=mode, staging=staging
                )
                self.assert_success(result)
                self.assertEqual(
                    [e["action"] for e in events],
                    [
                        "shell",
                        "stat",
                        "uri",
                        "copy",
                        "rpm_key",
                        "template",
                        "package",
                        "shell",
                    ],
                )
                download = next(e for e in events if e["action"] == "uri")
                self.assertEqual(download["delegate_to"], delegate)
                self.assertTrue(download["args"]["validate_certs"])
                key = next(e for e in events if e["action"] == "rpm_key")
                self.assertEqual(
                    key["args"]["fingerprint"],
                    "FF8AD1344597106ECE813B918A3872BF3228467C",
                )

    def test_preprovisioned_proxy_key_needs_no_network(self):
        result, events = self.run_role(
            air_gapped=True, mode="external", key_exists=True
        )
        self.assert_success(result)
        self.assertFalse(any(e["action"] in ("uri", "copy") for e in events))
        self.assertIn("rpm_key", [e["action"] for e in events])

    def test_untrusted_proxy_key_fails_before_repository_or_package_install(self):
        result, events = self.run_role(mode="nexus", bad_key=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Fingerprint mismatch", result.stdout)
        self.assertFalse(any(e["action"] in ("template", "package") for e in events))


if __name__ == "__main__":
    unittest.main()
