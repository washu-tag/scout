"""Tests for gen_secret_values: the value rules, the conditional keys, and the
contract staying in step with the on-prem Secret templates."""

import base64
import json
import re
from pathlib import Path

import pytest
import yaml

from gen_cluster_vars import load_required
from gen_secret_values import (
    CONDITIONAL,
    OPTIONAL,
    build,
    main,
    render_secret,
    validate,
)

HERE = Path(__file__).resolve().parent
DEPLOY = HERE.parents[1] / "deploy"
FIXTURE = HERE / "fixtures" / "secret-values.values.json"
CLUSTER_VARS = HERE / "fixtures" / "cluster-vars.values.json"
CONTRACT = DEPLOY / "required-secret-values.txt"
TEMPLATES = DEPLOY / "base" / "secrets-on-prem"


@pytest.fixture
def values():
    return json.loads(FIXTURE.read_text())


@pytest.fixture
def cluster_vars():
    return json.loads(CLUSTER_VARS.read_text())


@pytest.fixture
def contract():
    return load_required(CONTRACT)


def problems_for(values, cluster_vars, contract):
    return validate(values, cluster_vars, contract)[0]


def test_fixture_is_valid(values, cluster_vars, contract):
    assert validate(values, cluster_vars, contract) == ([], [])


def test_output_carries_every_required_and_provided_key(values, contract):
    data = build(values, contract)
    assert set(data) == set(values)
    absent = set(contract) - set(values)
    assert absent <= OPTIONAL | set(CONDITIONAL)


def test_rendered_secret_round_trips_exact_values(values, contract):
    hostile = dict(values, trino_internal_shared_secret='a "b" \\c #d: e $HOME ${x} é')
    doc = yaml.safe_load(
        render_secret(build(hostile, contract), "scout-secret-values", "flux-system")
    )
    assert doc["kind"] == "Secret"
    assert doc["metadata"]["namespace"] == "flux-system"
    assert doc["metadata"]["labels"] == {"reconcile.fluxcd.io/watch": "Enabled"}
    assert doc["metadata"]["annotations"] == {
        "kustomize.toolkit.fluxcd.io/substitute": "disabled"
    }
    assert doc["stringData"] == hostile


@pytest.mark.parametrize(
    "bad",
    [
        "it's",
        "line\nbreak",
        "carriage\rreturn",
        "tab\there",
        " leading",
        "trailing ",
        "zero​width",
        "line separator",
        "bell\x07",
    ],
)
def test_rejects_unsafe_characters(values, cluster_vars, contract, bad):
    probs = problems_for(dict(values, postgres_password=bad), cluster_vars, contract)
    assert len(probs) == 1 and probs[0].startswith("postgres_password: ")


def test_problems_never_echo_the_value(values, cluster_vars, contract):
    marker = "SECRETMARKER'\n"
    bad = {k: marker for k in contract}
    probs = problems_for(
        bad, dict(cluster_vars, keycloak_microsoft_enabled="true"), contract
    )
    assert probs and not any("SECRETMARKER" in p for p in probs)


@pytest.mark.parametrize(
    "key", ["postgres_password", "valkey_password", "superset_secret"]
)
def test_required_value_missing_or_empty(values, cluster_vars, contract, key):
    for broken in (
        {k: v for k, v in values.items() if k != key},
        dict(values, **{key: ""}),
    ):
        assert problems_for(broken, cluster_vars, contract) == [
            "{}: missing or empty".format(key)
        ]


def test_non_string_value_rejected(values, cluster_vars, contract):
    probs = problems_for(dict(values, postgres_password=1234), cluster_vars, contract)
    assert probs == ["postgres_password: must be a JSON string"]


def test_unknown_key_rejected_with_role_hint(values, cluster_vars, contract):
    probs = problems_for(
        dict(values, db_port="5432", hive_postgres_user="hivemeta"),
        cluster_vars,
        contract,
    )
    assert "db_port: not in required-secret-values.txt" in probs
    assert any(
        p.startswith("hive_postgres_user: not in") and "role name" in p for p in probs
    )


def test_conditional_keys_follow_their_flag(values, cluster_vars, contract):
    # github is on in the fixture: its keys are required.
    no_gh = {k: v for k, v in values.items() if not k.startswith("keycloak_gh_")}
    assert sorted(problems_for(no_gh, cluster_vars, contract)) == [
        "keycloak_gh_client_id: missing or empty while keycloak_github_enabled is on",
        "keycloak_gh_client_secret: missing or empty while keycloak_github_enabled is on",
    ]
    off = dict(cluster_vars, keycloak_github_enabled="false")
    assert problems_for(no_gh, off, contract) == []
    assert set(build(no_gh, contract)) == set(no_gh)
    # A YAML 1.1 truthy spelling turns the realm gate on too.
    ms_on = dict(cluster_vars, keycloak_microsoft_enabled="yes")
    assert len(problems_for(values, ms_on, contract)) == 3


def test_optional_s3_username_may_be_absent_or_empty(values, cluster_vars, contract):
    assert problems_for(dict(values, s3_username=""), cluster_vars, contract) == []
    assert "s3_username" not in build(dict(values, s3_username=""), contract)
    assert problems_for(dict(values, s3_username="ab"), cluster_vars, contract) == [
        "s3_username: is shorter than 3 characters"
    ]


@pytest.mark.parametrize("bad", ['quo"te', "back\\slash", "pässwörd"])
def test_valkey_password_is_json_safe_ascii(values, cluster_vars, contract, bad):
    probs = problems_for(dict(values, valkey_password=bad), cluster_vars, contract)
    assert len(probs) == 1 and probs[0].startswith(
        "valkey_password: must be printable ASCII"
    )


def test_minio_minimum_lengths(values, cluster_vars, contract):
    probs = problems_for(
        dict(values, s3_password="short", s3_lake_reader_secret="1234567"),
        cluster_vars,
        contract,
    )
    assert sorted(probs) == [
        "s3_lake_reader_secret: is shorter than 8 characters",
        "s3_password: is shorter than 8 characters",
    ]


@pytest.mark.parametrize(
    "cookie,ok",
    [
        ("0123456789abcdef", True),  # 16 raw bytes
        ("0123456789abcdef01234567", True),  # 24 raw bytes (openssl rand -hex 12)
        (
            "0123456789abcdef0123456789abcdef",
            True,
        ),  # 32 raw bytes (openssl rand -hex 16)
        (base64.urlsafe_b64encode(b"k" * 32).decode(), True),  # 44 chars, decodes to 32
        (base64.urlsafe_b64encode(b"k" * 32).decode().rstrip("="), True),  # unpadded
        (
            base64.b64encode(b"\xfb" * 32).decode(),
            False,
        ),  # std alphabet (+/) is not base64url
        ("0123456789abcdef0", False),  # 17 bytes, and not a valid encoding
    ],
)
def test_cookie_secret_aes_lengths(values, cluster_vars, contract, cookie, ok):
    probs = problems_for(
        dict(values, oauth2_proxy_cookie_secret=cookie), cluster_vars, contract
    )
    assert (probs == []) is ok


def test_placeholders_rejected_weak_defaults_warned(values, cluster_vars, contract):
    probs, warns = validate(
        dict(
            values,
            keycloak_gh_client_id="your-github-client",
            trino_keystore_password="trinokeystorepass",
        ),
        cluster_vars,
        contract,
    )
    assert probs == ["keycloak_gh_client_id: is the inventory.example.yaml placeholder"]
    assert warns == ["trino_keystore_password: is a weak Ansible default; rotate it"]


def test_cluster_var_layout_checks(values, cluster_vars, contract):
    same_ns = dict(
        cluster_vars, hive_namespace=cluster_vars["postgres_cluster_namespace"]
    )
    assert [p.split(":")[0] for p in problems_for(values, same_ns, contract)] == [
        "hive_namespace must differ from postgres_cluster_namespace"
    ]
    renamed = dict(
        cluster_vars, keycloak_postgres_user="kc", superset_postgres_user="superset"
    )
    probs = problems_for(values, renamed, contract)
    assert len(probs) == 1 and probs[0].startswith(
        "keycloak_postgres_user: the base fixes"
    )


def test_main_fails_closed(tmp_path, values, capsys):
    bad = tmp_path / "values.json"
    bad.write_text(json.dumps(dict(values, postgres_password="x'y")))
    with pytest.raises(SystemExit) as exc:
        main(["--values", str(bad), "--cluster-vars-values", str(CLUSTER_VARS)])
    assert exc.value.code == 1
    assert "postgres_password: contains '" in capsys.readouterr().err

    out = tmp_path / "scout-secret-values.yaml"
    main(
        [
            "--values",
            str(FIXTURE),
            "--cluster-vars-values",
            str(CLUSTER_VARS),
            "-o",
            str(out),
        ]
    )
    assert yaml.safe_load(out.read_text())["stringData"] == values


def _template_vars():
    text = "".join(p.read_text() for p in sorted(TEMPLATES.glob("*.yaml")))
    used = set(re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)", text))
    defaulted = set(re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*):=", text))
    return used, defaulted


def test_contract_matches_the_templates(contract):
    """required-secret-values.txt == the non-cluster-var names the templates use, the
    defaulted names == the optional + conditional keys, and no name is in both lists."""
    required_vars = set(load_required(DEPLOY / "required-vars.txt"))
    used, defaulted = _template_vars()
    assert not set(contract) & required_vars
    assert used - required_vars - {"sq"} == set(contract)
    assert defaulted == OPTIONAL | set(CONDITIONAL)
    assert set(CONDITIONAL.values()) <= required_vars
    assert len(contract) == len(set(contract))


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
