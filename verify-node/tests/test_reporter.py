from verify_node import CheckStatus, CheckResult, Reporter


class TestReporter:
    def test_empty_report(self):
        reporter = Reporter("test-node")
        output = reporter.format_output()
        assert "test-node" in output
        assert "0/0 passed" in output
        assert "RESULT: PASSED" in output

    def test_all_pass(self):
        reporter = Reporter("test-node")
        reporter.add(CheckResult("Mounts", CheckStatus.PASS, "/data is mounted (rw)"))
        reporter.add(CheckResult("Mounts", CheckStatus.PASS, "/shared is mounted (ro)"))
        output = reporter.format_output()
        assert "2/2 passed, 0 failed, 0 errors" in output
        assert "RESULT: PASSED" in output

    def test_with_failure(self):
        reporter = Reporter("test-node")
        reporter.add(CheckResult("Mounts", CheckStatus.PASS, "/data is mounted (rw)"))
        reporter.add(
            CheckResult(
                "Mounts",
                CheckStatus.FAIL,
                "/rad expected absent but is mounted",
                detail="Found mounted with options: rw,nosuid",
            )
        )
        output = reporter.format_output()
        assert "1/2 passed, 1 failed" in output
        assert "RESULT: FAILED" in output
        assert "Found mounted with options" in output

    def test_with_error(self):
        reporter = Reporter("test-node")
        reporter.add(
            CheckResult(
                "Resources",
                CheckStatus.ERROR,
                "GPU: nvidia-smi not available",
                detail="Command not found",
            )
        )
        output = reporter.format_output()
        assert "0 failed, 1 errors" in output
        assert "RESULT: FAILED" in output

    def test_category_headers(self):
        reporter = Reporter("test-node")
        reporter.add(CheckResult("Mounts", CheckStatus.PASS, "mount check"))
        reporter.add(CheckResult("Mounts", CheckStatus.PASS, "another mount"))
        reporter.add(CheckResult("Connectivity", CheckStatus.PASS, "tcp check"))
        reporter.add(CheckResult("Resources", CheckStatus.PASS, "cpu check"))
        output = reporter.format_output()
        # Category headers should appear once each
        assert output.count("--- Mounts ---") == 1
        assert output.count("--- Connectivity ---") == 1
        assert output.count("--- Resources ---") == 1

    def test_has_failures_false(self):
        reporter = Reporter("test-node")
        reporter.add(CheckResult("Mounts", CheckStatus.PASS, "ok"))
        assert reporter.has_failures() is False

    def test_has_failures_with_fail(self):
        reporter = Reporter("test-node")
        reporter.add(CheckResult("Mounts", CheckStatus.FAIL, "bad"))
        assert reporter.has_failures() is True

    def test_has_failures_with_error(self):
        reporter = Reporter("test-node")
        reporter.add(CheckResult("Mounts", CheckStatus.ERROR, "error"))
        assert reporter.has_failures() is True

    def test_multiline_detail(self):
        reporter = Reporter("test-node")
        reporter.add(
            CheckResult(
                "Resources",
                CheckStatus.FAIL,
                "GPU VRAM below minimum",
                detail="GPU 0: 16.0 GB\nGPU 1: 16.0 GB",
            )
        )
        output = reporter.format_output()
        lines = output.splitlines()
        # Both detail lines should be indented
        detail_lines = [l for l in lines if "GPU 0" in l or "GPU 1" in l]
        assert len(detail_lines) == 2
        for line in detail_lines:
            assert line.startswith("         ")

    def test_status_prefix_formatting(self):
        reporter = Reporter("test-node")
        reporter.add(CheckResult("Cat", CheckStatus.PASS, "msg1"))
        reporter.add(CheckResult("Cat", CheckStatus.FAIL, "msg2"))
        reporter.add(CheckResult("Cat", CheckStatus.ERROR, "msg3"))
        output = reporter.format_output()
        assert "[PASS] msg1" in output
        assert "[FAIL] msg2" in output
        assert "[ERROR] msg3" in output
