from __future__ import annotations

import os
from unittest.mock import patch

from verify_node import CheckStatus, MountChecker


class TestMountCheckerParsing:
    def test_parse_basic_mounts(self, proc_mounts_file):
        checker = MountChecker(proc_mounts_path=proc_mounts_file)
        mounts = checker._parse_mounts()
        assert "/scout/data" in mounts
        assert "/home_prod" in mounts
        assert "/shared" in mounts
        assert "rw" in mounts["/scout/data"]
        assert "ro" in mounts["/shared"]

    def test_parse_caches_result(self, proc_mounts_file):
        checker = MountChecker(proc_mounts_path=proc_mounts_file)
        mounts1 = checker._parse_mounts()
        mounts2 = checker._parse_mounts()
        assert mounts1 is mounts2

    def test_parse_missing_file(self, tmp_path):
        checker = MountChecker(proc_mounts_path=str(tmp_path / "nonexistent"))
        mounts = checker._parse_mounts()
        assert mounts == {}


class TestMountCheckerMounted:
    def test_rw_mount_present_and_writable(self, proc_mounts_file):
        checker = MountChecker(proc_mounts_path=proc_mounts_file)
        result = checker.check(
            {
                "path": "/scout/data",
                "state": "mounted",
                "writable": True,
            }
        )
        assert result.status == CheckStatus.PASS
        assert "rw" in result.message

    def test_rw_mount_not_present(self, proc_mounts_minimal):
        checker = MountChecker(proc_mounts_path=proc_mounts_minimal)
        result = checker.check(
            {
                "path": "/scout/data",
                "state": "mounted",
                "writable": True,
            }
        )
        assert result.status == CheckStatus.FAIL
        assert "not found" in result.message

    def test_rw_mount_but_ro(self, proc_mounts_file):
        checker = MountChecker(proc_mounts_path=proc_mounts_file)
        result = checker.check(
            {
                "path": "/shared",
                "state": "mounted",
                "writable": True,
            }
        )
        assert result.status == CheckStatus.FAIL
        assert "read-only" in result.message

    def test_ro_mount_present_and_readonly(self, proc_mounts_file):
        checker = MountChecker(proc_mounts_path=proc_mounts_file)
        result = checker.check(
            {
                "path": "/shared",
                "state": "mounted",
                "writable": False,
            }
        )
        assert result.status == CheckStatus.PASS
        assert "ro" in result.message

    def test_ro_mount_but_options_say_rw(self, proc_mounts_file):
        checker = MountChecker(proc_mounts_path=proc_mounts_file)
        # /rad is mounted rw in the test fixture
        result = checker.check(
            {
                "path": "/rad",
                "state": "mounted",
                "writable": False,
            }
        )
        assert result.status == CheckStatus.FAIL
        assert "read-write" in result.message


class TestMountCheckerAbsent:
    def test_absent_and_not_mounted(self, proc_mounts_minimal):
        checker = MountChecker(proc_mounts_path=proc_mounts_minimal)
        result = checker.check({"path": "/rad", "state": "absent"})
        assert result.status == CheckStatus.PASS
        assert "absent" in result.message

    def test_absent_but_mounted(self, proc_mounts_file):
        checker = MountChecker(proc_mounts_path=proc_mounts_file)
        result = checker.check({"path": "/rad", "state": "absent"})
        assert result.status == CheckStatus.FAIL
        assert "expected absent" in result.message


def _make_statvfs(total_gb):
    """Create a mock statvfs result with the given total size in GB."""
    block_size = 4096
    total_bytes = int(total_gb * 1024**3)
    total_blocks = total_bytes // block_size
    result = os.statvfs_result(
        (block_size, block_size, total_blocks, total_blocks, total_blocks, 0, 0, 0, 0, 255)
    )
    return result


class TestDiskSize:
    def test_disk_size_sufficient(self):
        checker = MountChecker()
        with patch("verify_node.os.statvfs", return_value=_make_statvfs(10000)):
            result = checker.check_disk_size("/scout/data", 9000)
        assert result.status == CheckStatus.PASS
        assert "10000" in result.message
        assert "9000" in result.message

    def test_disk_size_exact(self):
        checker = MountChecker()
        with patch("verify_node.os.statvfs", return_value=_make_statvfs(1000)):
            result = checker.check_disk_size("/var/lib/rancher", 1000)
        assert result.status == CheckStatus.PASS

    def test_disk_size_insufficient(self):
        checker = MountChecker()
        with patch("verify_node.os.statvfs", return_value=_make_statvfs(500)):
            result = checker.check_disk_size("/scout/data", 9000)
        assert result.status == CheckStatus.FAIL
        assert "500" in result.message
        assert "9000" in result.message

    def test_disk_size_path_not_found(self):
        checker = MountChecker()
        with patch("verify_node.os.statvfs", side_effect=OSError("No such file or directory")):
            result = checker.check_disk_size("/nonexistent", 1000)
        assert result.status == CheckStatus.ERROR
        assert "unable to determine" in result.message
