import json
import os
from unittest.mock import patch, MagicMock

from verify_node import main, run_checks, CheckStatus


class TestMain:
    def test_all_checks_pass(self, tmp_path, proc_mounts_file, proc_meminfo_256gb):
        config = {
            "hostname": "test-node",
            "mounts": [
                {"path": "/scout/data", "state": "mounted", "writable": True},
            ],
            "resources": {
                "min_cpu_cores": 2,
                "min_memory_gb": 8,
            },
        }
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(config))

        with (
            patch(
                "verify_node.MountChecker.__init__",
                lambda self, proc_mounts_path="/proc/mounts": (
                    setattr(self, "_proc_mounts_path", proc_mounts_file)
                    or setattr(self, "_mounts", None)
                ),
            ),
            patch(
                "verify_node.ResourceChecker.__init__",
                lambda self, proc_meminfo_path="/proc/meminfo": (
                    setattr(self, "_proc_meminfo_path", proc_meminfo_256gb)
                    or setattr(self, "_nvidia_smi_result", None)
                    or setattr(self, "_nvidia_smi_called", False)
                ),
            ),
            patch("verify_node.os.cpu_count", return_value=32),
        ):
            exit_code = main(["all", "--config", str(config_file)])

        assert exit_code == 0

    def test_check_failure_returns_1(self, tmp_path, proc_mounts_minimal):
        config = {
            "hostname": "test-node",
            "mounts": [
                {"path": "/scout/data", "state": "mounted", "writable": True},
            ],
        }
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(config))

        with patch(
            "verify_node.MountChecker.__init__",
            lambda self, proc_mounts_path="/proc/mounts": (
                setattr(self, "_proc_mounts_path", proc_mounts_minimal)
                or setattr(self, "_mounts", None)
            ),
        ):
            exit_code = main(["mounts", "--config", str(config_file)])

        assert exit_code == 1

    def test_config_error_returns_2(self):
        exit_code = main(["all", "--config-json", "{}"])
        assert exit_code == 2

    def test_invalid_json_returns_2(self):
        exit_code = main(["all", "--config-json", "{bad"])
        assert exit_code == 2

    def test_config_file_not_found_returns_2(self):
        exit_code = main(["all", "--config", "/nonexistent/config.json"])
        assert exit_code == 2

    def test_subcommand_mounts_only(self, tmp_path, proc_mounts_file):
        config = {
            "hostname": "test-node",
            "mounts": [
                {"path": "/scout/data", "state": "mounted", "writable": True},
            ],
            "resources": {
                "min_cpu_cores": 9999,
            },
        }
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(config))

        with patch(
            "verify_node.MountChecker.__init__",
            lambda self, proc_mounts_path="/proc/mounts": (
                setattr(self, "_proc_mounts_path", proc_mounts_file)
                or setattr(self, "_mounts", None)
            ),
        ):
            exit_code = main(["mounts", "--config", str(config_file)])

        # Should pass because resources check (which would fail) is skipped
        assert exit_code == 0


class TestRunChecks:
    def test_full_config_all_checks(self, proc_mounts_file, proc_meminfo_256gb):
        config = {
            "hostname": "test-node",
            "mounts": [
                {"path": "/scout/data", "state": "mounted", "writable": True},
                {"path": "/shared", "state": "mounted", "writable": False},
                {"path": "/staging_only_mount", "state": "absent"},
            ],
            "connectivity": {
                "timeout_seconds": 1,
                "checks": [
                    {
                        "host": "smtp.example.com",
                        "port": 25,
                        "expect": "reachable",
                        "description": "SMTP",
                    },
                ],
                "dns": [
                    {
                        "hostname": "scout.example.com",
                        "expect": "resolvable",
                        "description": "Scout DNS",
                    },
                ],
            },
            "resources": {
                "min_cpu_cores": 2,
                "min_memory_gb": 8,
                "gpus": {"count": 0},
            },
        }

        mock_sock = MagicMock()
        with (
            patch(
                "verify_node.MountChecker.__init__",
                lambda self, proc_mounts_path="/proc/mounts": (
                    setattr(self, "_proc_mounts_path", proc_mounts_file)
                    or setattr(self, "_mounts", None)
                ),
            ),
            patch("verify_node.socket.create_connection", return_value=mock_sock),
            patch("verify_node.socket.getaddrinfo") as mock_dns,
            patch(
                "verify_node.ResourceChecker.__init__",
                lambda self, proc_meminfo_path="/proc/meminfo": (
                    setattr(self, "_proc_meminfo_path", proc_meminfo_256gb)
                    or setattr(self, "_nvidia_smi_result", None)
                    or setattr(self, "_nvidia_smi_called", False)
                ),
            ),
            patch("verify_node.os.cpu_count", return_value=32),
        ):
            import socket

            mock_dns.return_value = [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.2.3.4", 0))
            ]
            reporter = run_checks(config, "all")

        assert not reporter.has_failures()
        # 3 mounts + 1 tcp + 1 dns + 1 cpu + 1 memory + 1 gpu count = 8 results
        assert len(reporter.results) == 8
        assert all(r.status == CheckStatus.PASS for r in reporter.results)

    def test_empty_config_no_checks(self):
        config = {"hostname": "test-node"}
        reporter = run_checks(config, "all")
        assert len(reporter.results) == 0
        assert not reporter.has_failures()

    def test_subcommand_filtering(self, proc_mounts_file):
        config = {
            "hostname": "test-node",
            "mounts": [
                {"path": "/scout/data", "state": "mounted", "writable": True},
            ],
            "resources": {
                "min_cpu_cores": 2,
            },
        }

        with patch(
            "verify_node.MountChecker.__init__",
            lambda self, proc_mounts_path="/proc/mounts": (
                setattr(self, "_proc_mounts_path", proc_mounts_file)
                or setattr(self, "_mounts", None)
            ),
        ):
            reporter = run_checks(config, "mounts")

        # Only mount checks should run, not resources
        assert len(reporter.results) == 1
        assert reporter.results[0].category == "Mounts"

    def test_disk_size_check_runs_for_mounted(self, proc_mounts_file):
        config = {
            "hostname": "test-node",
            "mounts": [
                {
                    "path": "/scout/data",
                    "state": "mounted",
                    "writable": True,
                    "min_size_gb": 100,
                },
                {"path": "/staging_only_mount", "state": "absent"},
            ],
        }

        mock_statvfs = os.statvfs_result(
            (4096, 4096, 50000000, 50000000, 50000000, 0, 0, 0, 0, 255)
        )
        with (
            patch(
                "verify_node.MountChecker.__init__",
                lambda self, proc_mounts_path="/proc/mounts": (
                    setattr(self, "_proc_mounts_path", proc_mounts_file)
                    or setattr(self, "_mounts", None)
                ),
            ),
            patch("verify_node.os.statvfs", return_value=mock_statvfs),
        ):
            reporter = run_checks(config, "mounts")

        # 1 mount check + 1 disk size check + 1 absent check = 3 results
        assert len(reporter.results) == 3
        disk_results = [r for r in reporter.results if "disk size" in r.message]
        assert len(disk_results) == 1
        assert disk_results[0].status == CheckStatus.PASS

    def test_disk_size_not_checked_for_absent(self, proc_mounts_minimal):
        config = {
            "hostname": "test-node",
            "mounts": [
                {"path": "/nonexistent", "state": "absent", "min_size_gb": 100},
            ],
        }

        with patch(
            "verify_node.MountChecker.__init__",
            lambda self, proc_mounts_path="/proc/mounts": (
                setattr(self, "_proc_mounts_path", proc_mounts_minimal)
                or setattr(self, "_mounts", None)
            ),
        ):
            reporter = run_checks(config, "mounts")

        # Only the absent check, no disk size check
        assert len(reporter.results) == 1
        assert "absent" in reporter.results[0].message
