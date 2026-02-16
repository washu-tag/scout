from __future__ import annotations

from unittest.mock import patch

import pytest

import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

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
    def test_rw_mount_present_and_writable(self, proc_mounts_file, tmp_path):
        # Point checker at our fake /proc/mounts
        checker = MountChecker(proc_mounts_path=proc_mounts_file)

        # Mock _write_test to succeed (no exception)
        with patch.object(checker, "_write_test"):
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

    def test_rw_mount_write_test_fails(self, proc_mounts_file):
        checker = MountChecker(proc_mounts_path=proc_mounts_file)
        with patch.object(
            checker, "_write_test", side_effect=OSError("Permission denied")
        ):
            result = checker.check(
                {
                    "path": "/scout/data",
                    "state": "mounted",
                    "writable": True,
                }
            )
        assert result.status == CheckStatus.FAIL
        assert "write test failed" in result.message

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

    def test_ro_mount_but_actually_writable(self, proc_mounts_file):
        checker = MountChecker(proc_mounts_path=proc_mounts_file)
        # /rad is mounted rw; write test should succeed, meaning it fails the ro check
        with patch.object(checker, "_write_test"):
            result = checker.check(
                {
                    "path": "/rad",
                    "state": "mounted",
                    "writable": False,
                }
            )
        assert result.status == CheckStatus.FAIL
        assert "writable" in result.message

    def test_ro_mount_options_say_rw_but_write_fails(self, proc_mounts_file):
        """Mount options say rw but actual write fails - treat as read-only (pass)."""
        checker = MountChecker(proc_mounts_path=proc_mounts_file)
        with patch.object(checker, "_write_test", side_effect=OSError("Read-only")):
            result = checker.check(
                {
                    "path": "/rad",
                    "state": "mounted",
                    "writable": False,
                }
            )
        assert result.status == CheckStatus.PASS


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


class TestWriteTest:
    def test_write_test_succeeds(self, tmp_path):
        """Write test should succeed on a writable directory."""
        MountChecker._write_test(str(tmp_path))

    def test_write_test_fails_on_nonexistent(self):
        """Write test should fail on a nonexistent directory."""
        with pytest.raises(OSError):
            MountChecker._write_test("/nonexistent/path/that/does/not/exist")
