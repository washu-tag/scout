"""Connection args for the ingest-status DB: static password, or an RDS IAM token."""

from unittest import mock

import pytest

from hl7scout import db

DB_ENV = {
    "DB_HOST": "db.example.internal",
    "DB_PORT": "5433",
    "DB_NAME": "ingest",
    "DB_USER": "scout_iam",
    "DB_PASSWORD": "static-pw",
}


@pytest.fixture(autouse=True)
def fresh_db_env(monkeypatch):
    """Each test starts from an unloaded module and only the env it sets."""
    for name in (
        *DB_ENV,
        "DB_IAM_AUTH",
        "DB_SSLMODE",
        "DB_SSLROOTCERT",
        "AWS_REGION",
        "AWS_DEFAULT_REGION",
        "AWS_PROFILE",
        "AWS_SESSION_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)
    for name, value in DB_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(db, "_connection_args", None)
    monkeypatch.setattr(db, "_iam_auth", False)
    db._rds_client.cache_clear()


@pytest.fixture
def rds_client(monkeypatch):
    client = mock.Mock()
    client.generate_db_auth_token.side_effect = ["token-1", "token-2"]
    monkeypatch.setattr(db, "_rds_client", lambda: client)
    return client


def test_password_auth_is_the_default(monkeypatch):
    monkeypatch.setattr(db, "_rds_client", mock.Mock(side_effect=AssertionError))

    args = db.get_db_connection_args()

    assert args == {
        "host": "db.example.internal",
        "port": "5433",
        "dbname": "ingest",
        "user": "scout_iam",
        "password": "static-pw",
    }
    assert db.get_db_connection_args() is args


@pytest.mark.parametrize("value", ["", "false", "0", "no"])
def test_falsy_iam_flag_keeps_password_auth(monkeypatch, value):
    monkeypatch.setenv("DB_IAM_AUTH", value)

    assert db.get_db_connection_args()["password"] == "static-pw"


@pytest.mark.parametrize("value", ["true", "True", "1", "yes"])
def test_iam_auth_mints_a_token_per_connection(monkeypatch, rds_client, value):
    monkeypatch.setenv("DB_IAM_AUTH", value)

    first = db.get_db_connection_args()
    second = db.get_db_connection_args()

    assert first == {
        "host": "db.example.internal",
        "port": "5433",
        "dbname": "ingest",
        "user": "scout_iam",
        "sslmode": "verify-full",
        "sslrootcert": "/etc/rds-ca/ca.pem",
        "password": "token-1",
    }
    assert second["password"] == "token-2"
    assert "password" not in db._connection_args
    rds_client.generate_db_auth_token.assert_called_with(
        DBHostname="db.example.internal", Port=5433, DBUsername="scout_iam"
    )


def test_iam_tls_overrides(monkeypatch, rds_client):
    monkeypatch.setenv("DB_IAM_AUTH", "true")
    monkeypatch.setenv("DB_SSLMODE", "require")
    monkeypatch.setenv("DB_SSLROOTCERT", "/certs/bundle.pem")

    args = db.get_db_connection_args()

    assert args["sslmode"] == "require"
    assert args["sslrootcert"] == "/certs/bundle.pem"


@pytest.mark.parametrize(
    "env, region",
    [
        ({"AWS_REGION": "us-west-2", "AWS_DEFAULT_REGION": "eu-west-1"}, "us-west-2"),
        ({"AWS_DEFAULT_REGION": "eu-west-1"}, "eu-west-1"),
    ],
)
def test_rds_client_region_from_env(monkeypatch, env, region):
    for name, value in env.items():
        monkeypatch.setenv(name, value)

    client = db._rds_client()

    assert client.meta.region_name == region
    assert client.meta.service_model.service_name == "rds"


def test_real_token_is_a_presigned_connect_url(monkeypatch):
    """botocore signs locally, so static fake credentials exercise the real API."""
    monkeypatch.setenv("DB_IAM_AUTH", "true")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIDEXAMPLE")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "fake-secret")

    token = db.get_db_connection_args()["password"]

    assert token.startswith("db.example.internal:5433/?Action=connect")
    assert "DBUser=scout_iam" in token
    assert "X-Amz-Signature=" in token
