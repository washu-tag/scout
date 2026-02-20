from __future__ import annotations

import io
import json
import pytest
from unittest.mock import patch

from verify_node import ConfigError, load_config, build_parser


def _make_args(**kwargs):
    """Create a namespace mimicking parsed CLI args."""
    defaults = {"config": None, "config_json": None, "config_stdin": False, "subcommand": "all"}
    defaults.update(kwargs)

    class Args:
        pass

    args = Args()
    for k, v in defaults.items():
        setattr(args, k, v)
    return args


class TestLoadConfig:
    def test_load_from_json_string(self):
        config = load_config(_make_args(config_json='{"hostname": "test"}'))
        assert config["hostname"] == "test"

    def test_load_from_file(self, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text('{"hostname": "test"}')
        config = load_config(_make_args(config=str(config_file)))
        assert config["hostname"] == "test"

    def test_invalid_json_string(self):
        with pytest.raises(ConfigError, match="Invalid JSON"):
            load_config(_make_args(config_json="{bad json"))

    def test_missing_config_file(self):
        with pytest.raises(ConfigError, match="not found"):
            load_config(_make_args(config="/nonexistent/path.json"))

    def test_invalid_json_file(self, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text("{bad json")
        with pytest.raises(ConfigError, match="Invalid JSON"):
            load_config(_make_args(config=str(config_file)))

    def test_load_from_stdin(self):
        with patch("verify_node.sys.stdin", io.StringIO('{"hostname": "test"}')):
            config = load_config(_make_args(config_stdin=True))
        assert config["hostname"] == "test"

    def test_invalid_json_stdin(self):
        with patch("verify_node.sys.stdin", io.StringIO("{bad json")):
            with pytest.raises(ConfigError, match="Invalid JSON from stdin"):
                load_config(_make_args(config_stdin=True))

    def test_no_config_provided(self):
        with pytest.raises(ConfigError, match="required"):
            load_config(_make_args())

    def test_config_not_object(self):
        with pytest.raises(ConfigError, match="JSON object"):
            load_config(_make_args(config_json="[]"))


class TestValidation:
    def test_missing_hostname(self):
        with pytest.raises(ConfigError, match="hostname"):
            load_config(_make_args(config_json='{"mounts": []}'))

    def test_mount_missing_path(self):
        with pytest.raises(ConfigError, match="path"):
            load_config(
                _make_args(
                    config_json=json.dumps(
                        {
                            "hostname": "test",
                            "mounts": [{"state": "mounted", "writable": True}],
                        }
                    )
                )
            )

    def test_mount_missing_state(self):
        with pytest.raises(ConfigError, match="state"):
            load_config(
                _make_args(
                    config_json=json.dumps(
                        {
                            "hostname": "test",
                            "mounts": [{"path": "/data"}],
                        }
                    )
                )
            )

    def test_mount_invalid_state(self):
        with pytest.raises(ConfigError, match="'mounted' or 'absent'"):
            load_config(
                _make_args(
                    config_json=json.dumps(
                        {
                            "hostname": "test",
                            "mounts": [{"path": "/data", "state": "unknown"}],
                        }
                    )
                )
            )

    def test_mounted_missing_writable(self):
        with pytest.raises(ConfigError, match="writable"):
            load_config(
                _make_args(
                    config_json=json.dumps(
                        {
                            "hostname": "test",
                            "mounts": [{"path": "/data", "state": "mounted"}],
                        }
                    )
                )
            )

    def test_absent_no_writable_needed(self):
        config = load_config(
            _make_args(
                config_json=json.dumps(
                    {
                        "hostname": "test",
                        "mounts": [{"path": "/data", "state": "absent"}],
                    }
                )
            )
        )
        assert len(config["mounts"]) == 1

    def test_connectivity_check_missing_fields(self):
        for field in ("host", "port", "expect"):
            check = {"host": "h", "port": 80, "expect": "reachable"}
            del check[field]
            with pytest.raises(ConfigError, match=field):
                load_config(
                    _make_args(
                        config_json=json.dumps(
                            {
                                "hostname": "test",
                                "connectivity": {"checks": [check]},
                            }
                        )
                    )
                )

    def test_connectivity_invalid_port(self):
        with pytest.raises(ConfigError, match="port must be an integer"):
            load_config(
                _make_args(
                    config_json=json.dumps(
                        {
                            "hostname": "test",
                            "connectivity": {
                                "checks": [
                                    {"host": "h", "port": "abc", "expect": "reachable"}
                                ]
                            },
                        }
                    )
                )
            )

    def test_connectivity_invalid_expect(self):
        with pytest.raises(ConfigError, match="'reachable' or 'unreachable'"):
            load_config(
                _make_args(
                    config_json=json.dumps(
                        {
                            "hostname": "test",
                            "connectivity": {
                                "checks": [{"host": "h", "port": 80, "expect": "maybe"}]
                            },
                        }
                    )
                )
            )

    def test_dns_missing_fields(self):
        for field in ("hostname", "expect"):
            dns = {"hostname": "h", "expect": "resolvable"}
            del dns[field]
            with pytest.raises(ConfigError, match=field):
                load_config(
                    _make_args(
                        config_json=json.dumps(
                            {
                                "hostname": "test",
                                "connectivity": {"dns": [dns]},
                            }
                        )
                    )
                )

    def test_dns_invalid_expect(self):
        with pytest.raises(ConfigError, match="'resolvable' or 'unresolvable'"):
            load_config(
                _make_args(
                    config_json=json.dumps(
                        {
                            "hostname": "test",
                            "connectivity": {
                                "dns": [{"hostname": "h", "expect": "maybe"}]
                            },
                        }
                    )
                )
            )

    def test_mount_invalid_min_size_gb(self):
        with pytest.raises(ConfigError, match="min_size_gb must be a positive number"):
            load_config(
                _make_args(
                    config_json=json.dumps(
                        {
                            "hostname": "test",
                            "mounts": [
                                {
                                    "path": "/data",
                                    "state": "mounted",
                                    "writable": True,
                                    "min_size_gb": "abc",
                                }
                            ],
                        }
                    )
                )
            )

    def test_mount_negative_min_size_gb(self):
        with pytest.raises(ConfigError, match="min_size_gb must be a positive number"):
            load_config(
                _make_args(
                    config_json=json.dumps(
                        {
                            "hostname": "test",
                            "mounts": [
                                {
                                    "path": "/data",
                                    "state": "mounted",
                                    "writable": True,
                                    "min_size_gb": -100,
                                }
                            ],
                        }
                    )
                )
            )

    def test_mount_valid_min_size_gb(self):
        config = load_config(
            _make_args(
                config_json=json.dumps(
                    {
                        "hostname": "test",
                        "mounts": [
                            {
                                "path": "/data",
                                "state": "mounted",
                                "writable": True,
                                "min_size_gb": 9000,
                            }
                        ],
                    }
                )
            )
        )
        assert config["mounts"][0]["min_size_gb"] == 9000

    def test_gpu_missing_count(self):
        with pytest.raises(ConfigError, match="count"):
            load_config(
                _make_args(
                    config_json=json.dumps(
                        {
                            "hostname": "test",
                            "resources": {"gpus": {"min_vram_gb": 80}},
                        }
                    )
                )
            )

    def test_valid_full_config(self):
        config = load_config(
            _make_args(
                config_json=json.dumps(
                    {
                        "hostname": "test-node",
                        "mounts": [
                            {"path": "/data", "state": "mounted", "writable": True},
                            {"path": "/shared", "state": "mounted", "writable": False},
                            {"path": "/rad", "state": "absent"},
                        ],
                        "connectivity": {
                            "timeout_seconds": 3,
                            "checks": [
                                {
                                    "host": "smtp.example.com",
                                    "port": 25,
                                    "expect": "reachable",
                                },
                                {
                                    "host": "8.8.8.8",
                                    "port": 443,
                                    "expect": "unreachable",
                                },
                            ],
                            "dns": [
                                {
                                    "hostname": "scout.example.com",
                                    "expect": "resolvable",
                                },
                            ],
                        },
                        "resources": {
                            "min_cpu_cores": 32,
                            "min_memory_gb": 256,
                            "gpus": {"count": 4, "min_vram_gb": 80},
                        },
                    }
                )
            )
        )
        assert config["hostname"] == "test-node"
        assert len(config["mounts"]) == 3


class TestBuildParser:
    def test_valid_subcommands(self):
        parser = build_parser()
        for cmd in ("all", "mounts", "connectivity", "resources"):
            args = parser.parse_args([cmd, "--config-json", "{}"])
            assert args.subcommand == cmd

    def test_config_stdin_flag(self):
        parser = build_parser()
        args = parser.parse_args(["all", "--config-stdin"])
        assert args.config_stdin is True
        assert args.config is None
        assert args.config_json is None

    def test_mutually_exclusive_config(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["all", "--config", "f", "--config-json", "{}"])

    def test_config_required(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["all"])
