#!/usr/bin/env python3
"""Node verification tool for Scout infrastructure.

Validates that a provisioned node meets expected requirements for mounts,
network connectivity, and hardware resources before Scout deployment.

Usage:
    python3 verify_node.py {all|mounts|connectivity|resources} --config /path/to/config.json
    python3 verify_node.py {all|mounts|connectivity|resources} --config-json '<json>'

Exit codes:
    0 = all checks passed
    1 = one or more checks failed
    2 = tool/config error
"""

from __future__ import annotations

import argparse
import dataclasses
import enum
import json
import os
import socket
import subprocess
import sys
from typing import Any


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


class CheckStatus(enum.Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    ERROR = "ERROR"


@dataclasses.dataclass
class CheckResult:
    category: str
    status: CheckStatus
    message: str
    detail: str = ""


# ---------------------------------------------------------------------------
# Reporter
# ---------------------------------------------------------------------------


class Reporter:
    """Accumulates check results and formats human-readable output."""

    def __init__(self, hostname: str) -> None:
        self.hostname = hostname
        self.results: list[CheckResult] = []

    def add(self, result: CheckResult) -> None:
        self.results.append(result)

    def format_output(self) -> str:
        lines: list[str] = [f"=== Node Verification: {self.hostname} ==="]
        current_category = ""

        for r in self.results:
            if r.category != current_category:
                current_category = r.category
                lines.append(f"\n--- {current_category} ---")
            lines.append(f"  [{r.status.value}] {r.message}")
            if r.detail:
                for detail_line in r.detail.splitlines():
                    lines.append(f"         {detail_line}")

        passed = sum(1 for r in self.results if r.status == CheckStatus.PASS)
        failed = sum(1 for r in self.results if r.status == CheckStatus.FAIL)
        errors = sum(1 for r in self.results if r.status == CheckStatus.ERROR)
        total = len(self.results)

        lines.append(
            f"\nSummary: {passed}/{total} passed, {failed} failed, {errors} errors"
        )
        if failed > 0 or errors > 0:
            lines.append("RESULT: FAILED")
        else:
            lines.append("RESULT: PASSED")

        return "\n".join(lines)

    def has_failures(self) -> bool:
        return any(
            r.status in (CheckStatus.FAIL, CheckStatus.ERROR) for r in self.results
        )


# ---------------------------------------------------------------------------
# Config loading & validation
# ---------------------------------------------------------------------------


class ConfigError(Exception):
    pass


def load_config(args: argparse.Namespace) -> dict[str, Any]:
    """Load and validate configuration from CLI args."""
    if args.config_json is not None:
        try:
            config = json.loads(args.config_json)
        except json.JSONDecodeError as e:
            raise ConfigError(f"Invalid JSON in --config-json: {e}") from e
    elif args.config is not None:
        try:
            with open(args.config) as f:
                config = json.load(f)
        except FileNotFoundError:
            raise ConfigError(f"Config file not found: {args.config}")
        except json.JSONDecodeError as e:
            raise ConfigError(f"Invalid JSON in {args.config}: {e}") from e
    else:
        raise ConfigError("One of --config or --config-json is required")

    if not isinstance(config, dict):
        raise ConfigError("Config must be a JSON object")

    _validate_config(config)
    return config


def _validate_config(config: dict[str, Any]) -> None:
    """Validate config structure and types."""
    if "hostname" not in config:
        raise ConfigError("Config must include 'hostname'")

    for mount in config.get("mounts", []):
        if "path" not in mount:
            raise ConfigError("Each mount must include 'path'")
        if "state" not in mount:
            raise ConfigError(f"Mount '{mount['path']}' must include 'state'")
        if mount["state"] not in ("mounted", "absent"):
            raise ConfigError(
                f"Mount '{mount['path']}' state must be 'mounted' or 'absent', "
                f"got '{mount['state']}'"
            )
        if mount["state"] == "mounted" and "writable" not in mount:
            raise ConfigError(
                f"Mount '{mount['path']}' with state 'mounted' must include 'writable'"
            )
        if "min_size_gb" in mount:
            try:
                val = float(mount["min_size_gb"])
                if val <= 0:
                    raise ValueError
            except (ValueError, TypeError):
                raise ConfigError(
                    f"Mount '{mount['path']}' min_size_gb must be a positive number, "
                    f"got '{mount['min_size_gb']}'"
                )

    connectivity = config.get("connectivity", {})
    for check in connectivity.get("checks", []):
        for field in ("host", "port", "expect"):
            if field not in check:
                raise ConfigError(f"Connectivity check must include '{field}'")
        try:
            int(check["port"])
        except (ValueError, TypeError):
            raise ConfigError(
                f"Connectivity check port must be an integer, got '{check['port']}'"
            )
        if check["expect"] not in ("reachable", "unreachable"):
            raise ConfigError(
                f"Connectivity check expect must be 'reachable' or 'unreachable', "
                f"got '{check['expect']}'"
            )

    for dns in connectivity.get("dns", []):
        for field in ("hostname", "expect"):
            if field not in dns:
                raise ConfigError(f"DNS check must include '{field}'")
        if dns["expect"] not in ("resolvable", "unresolvable"):
            raise ConfigError(
                f"DNS check expect must be 'resolvable' or 'unresolvable', "
                f"got '{dns['expect']}'"
            )

    resources = config.get("resources", {})
    gpus = resources.get("gpus")
    if gpus is not None:
        if "count" not in gpus:
            raise ConfigError("GPU config must include 'count'")


# ---------------------------------------------------------------------------
# MountChecker
# ---------------------------------------------------------------------------


class MountChecker:
    """Checks mount points against expected state."""

    CATEGORY = "Mounts"

    def __init__(self, proc_mounts_path: str = "/proc/mounts") -> None:
        self._proc_mounts_path = proc_mounts_path
        self._mounts: dict[str, set[str]] | None = None

    def _parse_mounts(self) -> dict[str, set[str]]:
        """Parse /proc/mounts into {path: {options}}."""
        if self._mounts is not None:
            return self._mounts

        mounts: dict[str, set[str]] = {}
        try:
            with open(self._proc_mounts_path) as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 4:
                        mount_point = parts[1]
                        options = set(parts[3].split(","))
                        mounts[mount_point] = options
        except FileNotFoundError:
            pass

        self._mounts = mounts
        return mounts

    def check(self, mount_config: dict[str, Any]) -> CheckResult:
        path = mount_config["path"]
        state = mount_config["state"]

        if state == "absent":
            return self._check_absent(path)
        else:
            writable = mount_config["writable"]
            return self._check_mounted(path, writable)

    def _check_absent(self, path: str) -> CheckResult:
        mounts = self._parse_mounts()
        if path in mounts:
            options_str = ",".join(sorted(mounts[path]))
            return CheckResult(
                category=self.CATEGORY,
                status=CheckStatus.FAIL,
                message=f"{path} expected absent but is mounted",
                detail=f"Found mounted with options: {options_str}",
            )
        return CheckResult(
            category=self.CATEGORY,
            status=CheckStatus.PASS,
            message=f"{path} is correctly absent",
        )

    def _check_mounted(self, path: str, writable: bool) -> CheckResult:
        mounts = self._parse_mounts()
        if path not in mounts:
            return CheckResult(
                category=self.CATEGORY,
                status=CheckStatus.FAIL,
                message=f"{path} expected mounted but not found in /proc/mounts",
            )

        options = mounts[path]

        if writable:
            return self._check_writable(path, options)
        else:
            return self._check_readonly(path, options)

    def _check_writable(self, path: str, options: set[str]) -> CheckResult:
        if "ro" in options:
            return CheckResult(
                category=self.CATEGORY,
                status=CheckStatus.FAIL,
                message=f"{path} expected writable but mounted read-only",
                detail=f"Mount options: {','.join(sorted(options))}",
            )

        return CheckResult(
            category=self.CATEGORY,
            status=CheckStatus.PASS,
            message=f"{path} is mounted (rw)",
        )

    def check_disk_size(self, path: str, min_size_gb: float) -> CheckResult:
        """Check that the filesystem at path has at least min_size_gb total size."""
        try:
            st = os.statvfs(path)
            total_bytes = st.f_blocks * st.f_frsize
            total_gb = total_bytes / (1024**3)

            if total_gb >= min_size_gb:
                return CheckResult(
                    category=self.CATEGORY,
                    status=CheckStatus.PASS,
                    message=f"{path} disk size: {total_gb:.0f} GB (minimum: {min_size_gb:.0f} GB)",
                )
            return CheckResult(
                category=self.CATEGORY,
                status=CheckStatus.FAIL,
                message=f"{path} disk size: {total_gb:.0f} GB (minimum: {min_size_gb:.0f} GB)",
                detail=f"Expected at least {min_size_gb:.0f} GB, found {total_gb:.0f} GB",
            )
        except OSError as e:
            return CheckResult(
                category=self.CATEGORY,
                status=CheckStatus.ERROR,
                message=f"{path} disk size: unable to determine",
                detail=str(e),
            )

    def _check_readonly(self, path: str, options: set[str]) -> CheckResult:
        if "ro" not in options:
            return CheckResult(
                category=self.CATEGORY,
                status=CheckStatus.FAIL,
                message=f"{path} expected read-only but mounted read-write",
                detail=f"Mount options: {','.join(sorted(options))}",
            )

        return CheckResult(
            category=self.CATEGORY,
            status=CheckStatus.PASS,
            message=f"{path} is mounted (ro)",
        )


# ---------------------------------------------------------------------------
# ConnectivityChecker
# ---------------------------------------------------------------------------


class ConnectivityChecker:
    """Checks TCP connectivity and DNS resolution."""

    CATEGORY = "Connectivity"

    def __init__(self, default_timeout: float = 5.0) -> None:
        self._default_timeout = default_timeout

    def check_tcp(
        self, check_config: dict[str, Any], timeout: float | None = None
    ) -> CheckResult:
        host = check_config["host"]
        port = int(check_config["port"])
        expect = check_config["expect"]
        description = check_config.get("description", f"{host}:{port}")
        timeout = timeout if timeout is not None else self._default_timeout

        reachable = False
        error_detail = ""
        try:
            with socket.create_connection((host, port), timeout=timeout) as sock:
                reachable = True
        except (socket.timeout, TimeoutError):
            error_detail = "Connection timed out"
        except ConnectionRefusedError:
            error_detail = "Connection refused"
        except OSError as e:
            error_detail = str(e)

        if expect == "reachable":
            if reachable:
                return CheckResult(
                    category=self.CATEGORY,
                    status=CheckStatus.PASS,
                    message=f"{description} - {host}:{port} reachable",
                )
            return CheckResult(
                category=self.CATEGORY,
                status=CheckStatus.FAIL,
                message=f"{description} - {host}:{port} expected reachable",
                detail=error_detail,
            )
        else:  # unreachable
            if not reachable:
                return CheckResult(
                    category=self.CATEGORY,
                    status=CheckStatus.PASS,
                    message=f"{description} - {host}:{port} correctly unreachable",
                )
            return CheckResult(
                category=self.CATEGORY,
                status=CheckStatus.FAIL,
                message=f"{description} - {host}:{port} expected unreachable but connection succeeded",
                detail="Air-gap violation: connection was established",
            )

    def check_dns(self, dns_config: dict[str, Any]) -> CheckResult:
        hostname = dns_config["hostname"]
        expect = dns_config["expect"]
        description = dns_config.get("description", hostname)

        resolved = False
        resolved_ip = ""
        error_detail = ""
        try:
            results = socket.getaddrinfo(
                hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM
            )
            if results:
                resolved = True
                resolved_ip = results[0][4][0]
        except socket.gaierror as e:
            error_detail = str(e)

        if expect == "resolvable":
            if resolved:
                return CheckResult(
                    category=self.CATEGORY,
                    status=CheckStatus.PASS,
                    message=f"{description} - {hostname} resolvable ({resolved_ip})",
                )
            return CheckResult(
                category=self.CATEGORY,
                status=CheckStatus.FAIL,
                message=f"{description} - {hostname} expected resolvable",
                detail=error_detail,
            )
        else:  # unresolvable
            if not resolved:
                return CheckResult(
                    category=self.CATEGORY,
                    status=CheckStatus.PASS,
                    message=f"{description} - {hostname} correctly unresolvable",
                )
            return CheckResult(
                category=self.CATEGORY,
                status=CheckStatus.FAIL,
                message=f"{description} - {hostname} expected unresolvable but resolved",
                detail=f"Resolved to {resolved_ip}",
            )


# ---------------------------------------------------------------------------
# ResourceChecker
# ---------------------------------------------------------------------------


class ResourceChecker:
    """Checks CPU, memory, and GPU resources."""

    CATEGORY = "Resources"
    MEMORY_TOLERANCE = 0.05  # 5% tolerance for kernel reservation
    NVIDIA_SMI_TIMEOUT = 10

    def __init__(self, proc_meminfo_path: str = "/proc/meminfo") -> None:
        self._proc_meminfo_path = proc_meminfo_path
        self._nvidia_smi_result: subprocess.CompletedProcess[str] | None = None
        self._nvidia_smi_called = False

    def check_cpu(self, min_cores: int) -> CheckResult:
        actual = os.cpu_count()
        if actual is None:
            return CheckResult(
                category=self.CATEGORY,
                status=CheckStatus.ERROR,
                message="CPU cores: unable to determine",
                detail="os.cpu_count() returned None",
            )

        if actual >= min_cores:
            return CheckResult(
                category=self.CATEGORY,
                status=CheckStatus.PASS,
                message=f"CPU cores: {actual} (minimum: {min_cores})",
            )
        return CheckResult(
            category=self.CATEGORY,
            status=CheckStatus.FAIL,
            message=f"CPU cores: {actual} (minimum: {min_cores})",
            detail=f"Expected at least {min_cores} cores, found {actual}",
        )

    def check_memory(self, min_gb: float) -> CheckResult:
        try:
            with open(self._proc_meminfo_path) as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        parts = line.split()
                        kb = int(parts[1])
                        actual_gb = kb / (1024 * 1024)
                        break
                else:
                    return CheckResult(
                        category=self.CATEGORY,
                        status=CheckStatus.ERROR,
                        message="Memory: MemTotal not found in /proc/meminfo",
                    )
        except FileNotFoundError:
            return CheckResult(
                category=self.CATEGORY,
                status=CheckStatus.ERROR,
                message="Memory: /proc/meminfo not found",
            )

        threshold = min_gb * (1 - self.MEMORY_TOLERANCE)
        if actual_gb >= threshold:
            return CheckResult(
                category=self.CATEGORY,
                status=CheckStatus.PASS,
                message=f"Memory: {actual_gb:.1f} GB (minimum: {min_gb} GB, 5% tolerance)",
            )
        return CheckResult(
            category=self.CATEGORY,
            status=CheckStatus.FAIL,
            message=f"Memory: {actual_gb:.1f} GB (minimum: {min_gb} GB, 5% tolerance)",
            detail=f"Expected at least {threshold:.1f} GB (after tolerance), found {actual_gb:.1f} GB",
        )

    def _run_nvidia_smi(self) -> subprocess.CompletedProcess[str] | None:
        """Run nvidia-smi once and cache the result."""
        if self._nvidia_smi_called:
            return self._nvidia_smi_result

        self._nvidia_smi_called = True
        try:
            self._nvidia_smi_result = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=index,name,memory.total",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=self.NVIDIA_SMI_TIMEOUT,
            )
        except FileNotFoundError:
            self._nvidia_smi_result = None
        except subprocess.TimeoutExpired:
            self._nvidia_smi_result = None

        return self._nvidia_smi_result

    def _parse_gpu_info(self) -> list[dict[str, Any]] | None:
        """Parse nvidia-smi output into list of GPU info dicts.

        Returns None if nvidia-smi is not available or failed.
        """
        result = self._run_nvidia_smi()
        if result is None:
            return None
        if result.returncode != 0:
            return None

        gpus: list[dict[str, Any]] = []
        for line in result.stdout.strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 3:
                try:
                    gpus.append(
                        {
                            "index": int(parts[0]),
                            "name": parts[1],
                            "vram_mb": float(parts[2]),
                        }
                    )
                except (ValueError, IndexError):
                    continue
        return gpus

    def check_gpu_count(self, expected_count: int) -> CheckResult:
        gpus = self._parse_gpu_info()

        if expected_count == 0:
            if gpus is None or len(gpus) == 0:
                return CheckResult(
                    category=self.CATEGORY,
                    status=CheckStatus.PASS,
                    message="GPUs: 0 (expected: 0)",
                )
            return CheckResult(
                category=self.CATEGORY,
                status=CheckStatus.FAIL,
                message=f"GPUs: {len(gpus)} (expected: 0)",
                detail="Expected no GPUs but nvidia-smi reported some",
            )

        if gpus is None:
            return CheckResult(
                category=self.CATEGORY,
                status=CheckStatus.ERROR,
                message=f"GPUs: nvidia-smi not available (expected: {expected_count})",
                detail="nvidia-smi not found or timed out",
            )

        actual_count = len(gpus)
        if actual_count >= expected_count:
            return CheckResult(
                category=self.CATEGORY,
                status=CheckStatus.PASS,
                message=f"GPUs: {actual_count} (expected: {expected_count})",
            )
        return CheckResult(
            category=self.CATEGORY,
            status=CheckStatus.FAIL,
            message=f"GPUs: {actual_count} (expected: {expected_count})",
            detail=f"nvidia-smi reported {actual_count} GPUs, expected {expected_count}",
        )

    def check_gpu_vram(self, min_vram_gb: float) -> CheckResult:
        gpus = self._parse_gpu_info()

        if gpus is None:
            return CheckResult(
                category=self.CATEGORY,
                status=CheckStatus.ERROR,
                message="GPU VRAM: nvidia-smi not available",
                detail="nvidia-smi not found or timed out",
            )

        if len(gpus) == 0:
            return CheckResult(
                category=self.CATEGORY,
                status=CheckStatus.ERROR,
                message="GPU VRAM: no GPUs detected",
            )

        min_vram_mb = min_vram_gb * 1024
        failing_gpus: list[str] = []
        for gpu in gpus:
            if gpu["vram_mb"] < min_vram_mb:
                actual_gb = gpu["vram_mb"] / 1024
                failing_gpus.append(
                    f"GPU {gpu['index']} ({gpu['name']}): {actual_gb:.1f} GB"
                )

        if not failing_gpus:
            return CheckResult(
                category=self.CATEGORY,
                status=CheckStatus.PASS,
                message=f"GPU VRAM: all GPUs >= {min_vram_gb} GB",
            )

        return CheckResult(
            category=self.CATEGORY,
            status=CheckStatus.FAIL,
            message=f"GPU VRAM: some GPUs below {min_vram_gb} GB minimum",
            detail="\n".join(failing_gpus),
        )


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def run_checks(config: dict[str, Any], subcommand: str) -> Reporter:
    """Run requested checks and return reporter with results."""
    reporter = Reporter(config.get("hostname", "unknown"))
    run_all = subcommand == "all"

    if run_all or subcommand == "mounts":
        mount_checker = MountChecker()
        for mount_config in config.get("mounts", []):
            reporter.add(mount_checker.check(mount_config))
            if mount_config.get("state") == "mounted" and "min_size_gb" in mount_config:
                reporter.add(
                    mount_checker.check_disk_size(
                        mount_config["path"], mount_config["min_size_gb"]
                    )
                )

    if run_all or subcommand == "connectivity":
        connectivity = config.get("connectivity", {})
        timeout = connectivity.get("timeout_seconds", 5.0)
        conn_checker = ConnectivityChecker(default_timeout=timeout)

        for check_config in connectivity.get("checks", []):
            reporter.add(conn_checker.check_tcp(check_config))

        for dns_config in connectivity.get("dns", []):
            reporter.add(conn_checker.check_dns(dns_config))

    if run_all or subcommand == "resources":
        resources = config.get("resources", {})
        resource_checker = ResourceChecker()

        if "min_cpu_cores" in resources:
            reporter.add(resource_checker.check_cpu(resources["min_cpu_cores"]))

        if "min_memory_gb" in resources:
            reporter.add(resource_checker.check_memory(resources["min_memory_gb"]))

        gpus = resources.get("gpus")
        if gpus is not None:
            reporter.add(resource_checker.check_gpu_count(gpus["count"]))
            if "min_vram_gb" in gpus and gpus["count"] > 0:
                reporter.add(resource_checker.check_gpu_vram(gpus["min_vram_gb"]))

    return reporter


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify node state for Scout deployment",
    )
    parser.add_argument(
        "subcommand",
        choices=["all", "mounts", "connectivity", "resources"],
        help="Which checks to run",
    )

    config_group = parser.add_mutually_exclusive_group(required=True)
    config_group.add_argument(
        "--config",
        help="Path to JSON config file",
    )
    config_group.add_argument(
        "--config-json",
        help="Inline JSON config string",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        config = load_config(args)
    except ConfigError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        return 2

    reporter = run_checks(config, args.subcommand)
    print(reporter.format_output())

    return 1 if reporter.has_failures() else 0


if __name__ == "__main__":
    sys.exit(main())
