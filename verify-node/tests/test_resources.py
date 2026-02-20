import subprocess
from unittest.mock import patch, MagicMock

from verify_node import CheckStatus, ResourceChecker

NVIDIA_SMI_4GPU = """\
0, NVIDIA A100-SXM4-80GB, 81920
1, NVIDIA A100-SXM4-80GB, 81920
2, NVIDIA A100-SXM4-80GB, 81920
3, NVIDIA A100-SXM4-80GB, 81920
"""

NVIDIA_SMI_2GPU_MIXED = """\
0, NVIDIA A100-SXM4-80GB, 81920
1, NVIDIA T4, 16384
"""

NVIDIA_SMI_EMPTY = ""


class TestCPU:
    def test_cpu_sufficient(self):
        checker = ResourceChecker()
        with patch("verify_node.os.cpu_count", return_value=32):
            result = checker.check_cpu(32)
        assert result.status == CheckStatus.PASS

    def test_cpu_more_than_minimum(self):
        checker = ResourceChecker()
        with patch("verify_node.os.cpu_count", return_value=64):
            result = checker.check_cpu(32)
        assert result.status == CheckStatus.PASS

    def test_cpu_insufficient(self):
        checker = ResourceChecker()
        with patch("verify_node.os.cpu_count", return_value=16):
            result = checker.check_cpu(32)
        assert result.status == CheckStatus.FAIL

    def test_cpu_unknown(self):
        checker = ResourceChecker()
        with patch("verify_node.os.cpu_count", return_value=None):
            result = checker.check_cpu(32)
        assert result.status == CheckStatus.ERROR


class TestMemory:
    def test_memory_256gb_passes_256_minimum(self, proc_meminfo_256gb):
        checker = ResourceChecker(proc_meminfo_path=proc_meminfo_256gb)
        result = checker.check_memory(256)
        assert result.status == CheckStatus.PASS
        assert "251" in result.message  # ~251GB after kernel reservation

    def test_memory_8gb_fails_256_minimum(self, proc_meminfo_8gb):
        checker = ResourceChecker(proc_meminfo_path=proc_meminfo_8gb)
        result = checker.check_memory(256)
        assert result.status == CheckStatus.FAIL

    def test_memory_8gb_passes_8_minimum(self, proc_meminfo_8gb):
        checker = ResourceChecker(proc_meminfo_path=proc_meminfo_8gb)
        result = checker.check_memory(8)
        assert result.status == CheckStatus.PASS

    def test_memory_missing_file(self, tmp_path):
        checker = ResourceChecker(proc_meminfo_path=str(tmp_path / "nonexistent"))
        result = checker.check_memory(8)
        assert result.status == CheckStatus.ERROR

    def test_memory_no_memtotal(self, tmp_path):
        p = tmp_path / "meminfo"
        p.write_text("MemFree: 1000 kB\n")
        checker = ResourceChecker(proc_meminfo_path=str(p))
        result = checker.check_memory(8)
        assert result.status == CheckStatus.ERROR

    def test_memory_tolerance(self, proc_meminfo_256gb):
        """~251GB actual should pass 256GB minimum (within 5% tolerance)."""
        checker = ResourceChecker(proc_meminfo_path=proc_meminfo_256gb)
        result = checker.check_memory(256)
        assert result.status == CheckStatus.PASS


def _make_nvidia_smi_result(stdout: str, returncode: int = 0):
    return subprocess.CompletedProcess(
        args=["nvidia-smi"], returncode=returncode, stdout=stdout, stderr=""
    )


class TestGPUCount:
    def test_4_gpus_expected_4(self):
        checker = ResourceChecker()
        with patch.object(
            checker,
            "_run_nvidia_smi",
            return_value=_make_nvidia_smi_result(NVIDIA_SMI_4GPU),
        ):
            result = checker.check_gpu_count(4)
        assert result.status == CheckStatus.PASS

    def test_4_gpus_expected_2(self):
        checker = ResourceChecker()
        with patch.object(
            checker,
            "_run_nvidia_smi",
            return_value=_make_nvidia_smi_result(NVIDIA_SMI_4GPU),
        ):
            result = checker.check_gpu_count(2)
        assert result.status == CheckStatus.PASS

    def test_2_gpus_expected_4(self):
        checker = ResourceChecker()
        with patch.object(
            checker,
            "_run_nvidia_smi",
            return_value=_make_nvidia_smi_result(NVIDIA_SMI_2GPU_MIXED),
        ):
            result = checker.check_gpu_count(4)
        assert result.status == CheckStatus.FAIL

    def test_no_nvidia_smi_expected_0(self):
        checker = ResourceChecker()
        with patch.object(checker, "_run_nvidia_smi", return_value=None):
            result = checker.check_gpu_count(0)
        assert result.status == CheckStatus.PASS

    def test_no_nvidia_smi_expected_4(self):
        checker = ResourceChecker()
        with patch.object(checker, "_run_nvidia_smi", return_value=None):
            result = checker.check_gpu_count(4)
        assert result.status == CheckStatus.ERROR

    def test_nvidia_smi_empty_expected_0(self):
        checker = ResourceChecker()
        with patch.object(
            checker,
            "_run_nvidia_smi",
            return_value=_make_nvidia_smi_result(NVIDIA_SMI_EMPTY),
        ):
            result = checker.check_gpu_count(0)
        assert result.status == CheckStatus.PASS

    def test_nvidia_smi_empty_expected_4(self):
        checker = ResourceChecker()
        with patch.object(
            checker,
            "_run_nvidia_smi",
            return_value=_make_nvidia_smi_result(NVIDIA_SMI_EMPTY),
        ):
            result = checker.check_gpu_count(4)
        assert result.status == CheckStatus.FAIL

    def test_nvidia_smi_nonzero_exit(self):
        checker = ResourceChecker()
        result_obj = _make_nvidia_smi_result("", returncode=1)
        with patch.object(checker, "_run_nvidia_smi", return_value=result_obj):
            result = checker.check_gpu_count(4)
        assert result.status == CheckStatus.ERROR


class TestGPUVRAM:
    def test_all_gpus_above_minimum(self):
        checker = ResourceChecker()
        with patch.object(
            checker,
            "_run_nvidia_smi",
            return_value=_make_nvidia_smi_result(NVIDIA_SMI_4GPU),
        ):
            result = checker.check_gpu_vram(80)
        assert result.status == CheckStatus.PASS

    def test_mixed_gpus_one_below_minimum(self):
        checker = ResourceChecker()
        with patch.object(
            checker,
            "_run_nvidia_smi",
            return_value=_make_nvidia_smi_result(NVIDIA_SMI_2GPU_MIXED),
        ):
            result = checker.check_gpu_vram(80)
        assert result.status == CheckStatus.FAIL
        assert "T4" in result.detail

    def test_no_nvidia_smi(self):
        checker = ResourceChecker()
        with patch.object(checker, "_run_nvidia_smi", return_value=None):
            result = checker.check_gpu_vram(80)
        assert result.status == CheckStatus.ERROR

    def test_no_gpus_detected(self):
        checker = ResourceChecker()
        with patch.object(
            checker,
            "_run_nvidia_smi",
            return_value=_make_nvidia_smi_result(NVIDIA_SMI_EMPTY),
        ):
            result = checker.check_gpu_vram(80)
        assert result.status == CheckStatus.ERROR


class TestNvidiaSmiCaching:
    def test_nvidia_smi_called_once(self):
        checker = ResourceChecker()
        mock_run = MagicMock(return_value=_make_nvidia_smi_result(NVIDIA_SMI_4GPU))
        with patch("verify_node.subprocess.run", mock_run):
            checker.check_gpu_count(4)
            checker.check_gpu_vram(80)
        mock_run.assert_called_once()

    def test_nvidia_smi_file_not_found_cached(self):
        checker = ResourceChecker()
        mock_run = MagicMock(side_effect=FileNotFoundError)
        with patch("verify_node.subprocess.run", mock_run):
            checker.check_gpu_count(0)
            checker.check_gpu_count(0)
        mock_run.assert_called_once()

    def test_nvidia_smi_timeout_cached(self):
        checker = ResourceChecker()
        mock_run = MagicMock(
            side_effect=subprocess.TimeoutExpired(cmd="nvidia-smi", timeout=10)
        )
        with patch("verify_node.subprocess.run", mock_run):
            checker.check_gpu_count(4)
            checker.check_gpu_count(4)
        mock_run.assert_called_once()
