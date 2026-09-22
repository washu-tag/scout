"""Tests for the Superset RDS IAM override shipped in the config artifact
(deploy/base/superset/server/superset_rds_iam.py), with boto3 and sqlalchemy stubbed.
"""

import importlib.util
import sys
import types
from pathlib import Path

import pytest

MODULE = (
    Path(__file__).resolve().parents[2]
    / "deploy/base/superset/server/superset_rds_iam.py"
)
ENV = {
    "DB_IAM_AUTH": "true",
    "DB_HOST": "db.example.internal",
    "DB_PORT": "5432",
    "DB_USER": "superset_iam",
    "DB_NAME": "superset",
    "AWS_REGION": "us-east-1",
}


class FakeClient:
    def __init__(self, region):
        self.region = region
        self.calls = []

    def generate_db_auth_token(self, **kwargs):
        self.calls.append(kwargs)
        return "token-for-" + kwargs["DBUsername"]


@pytest.fixture
def load(monkeypatch):
    """Import the override under ENV (+ overrides); returns (module, listeners, clients)."""

    def _load(**env):
        listeners, clients = [], []

        def client(service, region_name=None):
            assert service == "rds"
            clients.append(FakeClient(region_name))
            return clients[-1]

        boto3 = types.ModuleType("boto3")
        boto3.session = types.SimpleNamespace(
            Session=lambda: types.SimpleNamespace(client=client)
        )

        def listens_for(target, name):
            def register(fn):
                listeners.append((target, name, fn))
                return fn

            return register

        sqlalchemy = types.ModuleType("sqlalchemy")
        sqlalchemy.event = types.SimpleNamespace(listens_for=listens_for)
        engine_mod = types.ModuleType("sqlalchemy.engine")
        engine_mod.Engine = type("Engine", (), {})
        for name, mod in (
            ("boto3", boto3),
            ("sqlalchemy", sqlalchemy),
            ("sqlalchemy.engine", engine_mod),
        ):
            monkeypatch.setitem(sys.modules, name, mod)
        for key in ("DB_SSLMODE", "DB_SSLROOTCERT", "AWS_DEFAULT_REGION"):
            monkeypatch.delenv(key, raising=False)
        for key, value in {**ENV, **env}.items():
            if value is None:
                monkeypatch.delenv(key, raising=False)
            else:
                monkeypatch.setenv(key, value)
        spec = importlib.util.spec_from_file_location("superset_rds_iam", MODULE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module, listeners, clients

    return _load


def connect(listeners, dialect="postgresql", **cparams):
    params = {
        "host": ENV["DB_HOST"],
        "port": 5432,
        "user": ENV["DB_USER"],
        "database": "superset",
        **cparams,
    }
    for _, _, fn in listeners:
        fn(types.SimpleNamespace(name=dialect), None, [], params)
    return params


@pytest.mark.parametrize("flag", [None, "", "false", "0"])
def test_inert_unless_enabled(load, flag):
    module, listeners, clients = load(DB_IAM_AUTH=flag)
    assert listeners == [] and clients == []
    assert not hasattr(module, "SQLALCHEMY_DATABASE_URI")


def test_registers_class_level_do_connect_and_passwordless_uri(load):
    module, listeners, _ = load()
    ((target, name, _),) = listeners
    assert target is sys.modules["sqlalchemy.engine"].Engine
    assert name == "do_connect"
    assert module.SQLALCHEMY_DATABASE_URI == (
        "postgresql+psycopg2://superset_iam@db.example.internal:5432/superset"
    )


def test_metadata_connection_gets_token_over_verified_tls(load):
    _, listeners, clients = load()
    params = connect(listeners)
    assert params["password"] == "token-for-superset_iam"
    assert params["sslmode"] == "verify-full"
    assert params["sslrootcert"] == "/etc/rds-ca/ca.pem"
    (client,) = clients
    assert client.region == "us-east-1"
    assert client.calls == [
        {
            "DBHostname": "db.example.internal",
            "Port": 5432,
            "DBUsername": "superset_iam",
            "Region": "us-east-1",
        }
    ]


@pytest.mark.parametrize(
    "dialect,cparams",
    [
        ("trino", {}),
        ("postgresql", {"host": "other.example.internal"}),
        ("postgresql", {"user": "someone_else"}),
    ],
)
def test_other_engines_untouched(load, dialect, cparams):
    _, listeners, clients = load()
    params = connect(listeners, dialect=dialect, **cparams)
    assert not {"password", "sslmode", "sslrootcert"} & set(params)
    assert clients == []


def test_tls_overrides_and_region_fallback(load):
    _, listeners, clients = load(
        AWS_REGION=None,
        AWS_DEFAULT_REGION="eu-west-1",
        DB_SSLMODE="require",
        DB_SSLROOTCERT="/tmp/ca.pem",
    )
    params = connect(listeners)
    assert (params["sslmode"], params["sslrootcert"]) == ("require", "/tmp/ca.pem")
    assert clients[0].region == "eu-west-1"
    assert clients[0].calls[0]["Region"] == "eu-west-1"


def test_one_rds_client_per_process(load, monkeypatch):
    module, listeners, clients = load()
    connect(listeners)
    connect(listeners)
    assert len(clients) == 1
    monkeypatch.setattr(module.os, "getpid", lambda: -1)  # a forked worker
    connect(listeners)
    assert len(clients) == 2
