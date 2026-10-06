"""Tests for gen_secret_values: the value rules, the conditional keys, and the
contract staying in step with the on-prem Secret templates."""

import base64
import json
import os
from pathlib import Path

import pytest
import yaml

import gen_cluster_vars
from check_secret_templates import VAR, templates
from gen_cluster_vars import load_required
from gen_secret_values import (
    DEFAULT_REQUIRED,
    build,
    load_contract,
    main,
    render_secret,
    validate,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FIXTURE = FIXTURES / "secret-values.values.json"
CLUSTER_VARS = FIXTURES / "cluster-vars.values.json"
CONTRACT = load_contract(DEFAULT_REQUIRED)
REQUIRED_VARS = set(load_required(gen_cluster_vars.DEFAULT_REQUIRED))


@pytest.fixture
def values():
    return json.loads(FIXTURE.read_text())


@pytest.fixture
def cluster_vars():
    return json.loads(CLUSTER_VARS.read_text())


def problems_for(values, cluster_vars):
    return validate(values, cluster_vars, CONTRACT)[0]


def test_fixture_is_valid(values, cluster_vars):
    assert validate(values, cluster_vars, CONTRACT) == ([], [])
    assert set(build(values, CONTRACT)) == set(values)


def test_rendered_secret_round_trips_exact_values(values):
    hostile = dict(values, trino_internal_shared_secret='a "b" \\c #d: e $HOME ${x} é')
    doc = yaml.safe_load(
        render_secret(build(hostile, CONTRACT), "scout-secret-values", "flux-system")
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
        "bell\x07",
        "zero" + chr(0x200B) + "width",
        "line" + chr(0x2028) + "separator",
        "non" + chr(0xFFFF) + "character",
        "lone" + chr(0xDC80) + "surrogate",
        "inner space",
        "pässwörd",
    ],
)
def test_rejects_unsafe_characters(values, cluster_vars, bad):
    probs = problems_for(dict(values, postgres_password=bad), cluster_vars)
    assert len(probs) == 1 and probs[0].startswith("postgres_password: ")


def test_problems_never_echo_the_value(cluster_vars):
    marker = "SECRETMARKER'\n"
    probs = problems_for(
        {k: marker for k in CONTRACT},
        dict(cluster_vars, keycloak_microsoft_enabled="true"),
    )
    assert probs and not any("SECRETMARKER" in p for p in probs)


def test_required_value_missing_or_empty(values, cluster_vars):
    for broken in (
        {k: v for k, v in values.items() if k != "postgres_password"},
        dict(values, postgres_password=""),
    ):
        assert problems_for(broken, cluster_vars) == [
            "postgres_password: missing or empty"
        ]


def test_non_string_value_rejected(values, cluster_vars):
    probs = problems_for(dict(values, postgres_password=1234), cluster_vars)
    assert probs == ["postgres_password: must be a JSON string"]


def test_unknown_key_rejected_with_fixed_name_hint(values, cluster_vars):
    probs = problems_for(
        dict(values, db_port="5432", hive_postgres_user="hivemeta"), cluster_vars
    )
    assert "db_port: not in required-secret-values.txt" in probs
    assert any(
        p.startswith("hive_postgres_user: not in") and "fixes this name" in p
        for p in probs
    )


def test_conditional_keys_follow_their_flag(values, cluster_vars):
    # github is on in the fixture: its keys are required.
    no_gh = {k: v for k, v in values.items() if not k.startswith("keycloak_gh_")}
    assert sorted(problems_for(no_gh, cluster_vars)) == [
        "keycloak_gh_client_id: missing or empty while keycloak_github_enabled is on",
        "keycloak_gh_client_secret: missing or empty while keycloak_github_enabled is on",
    ]
    off = dict(cluster_vars, keycloak_github_enabled="false")
    assert problems_for(no_gh, off) == []
    # With the flag off, a supplied value is a mistake (Ansible enabled the broker
    # whenever its client id was set), not something to carry silently.
    assert sorted(problems_for(values, off)) == [
        "keycloak_gh_client_id: set while keycloak_github_enabled is off; turn the flag on or drop the value",
        "keycloak_gh_client_secret: set while keycloak_github_enabled is off; turn the flag on or drop the value",
    ]
    # A YAML 1.1 truthy spelling turns the realm gate on too.
    ms_on = dict(cluster_vars, keycloak_microsoft_enabled="yes")
    assert len(problems_for(values, ms_on)) == 3


def test_minio_settings_are_checked_as_cluster_vars(values, cluster_vars):
    assert problems_for(dict(values, s3_username="minio"), cluster_vars) == [
        "s3_username: not in required-secret-values.txt"
    ]
    minio = ("s3_username", "minio_oidc_enabled")
    defaulted = {k: v for k, v in cluster_vars.items() if k not in minio}
    assert problems_for(values, defaulted) == []
    assert problems_for(values, dict(cluster_vars, minio_oidc_enabled="off")) == []
    assert problems_for(values, dict(cluster_vars, minio_oidc_enabled="no")) == [
        "minio_oidc_enabled: must be on, off, true or false"
    ]
    assert problems_for(values, dict(cluster_vars, s3_username="ab")) == [
        "s3_username: is shorter than 3 characters"
    ]
    assert problems_for(values, dict(cluster_vars, s3_username='mi"nio')) == [
        "s3_username: may use only A-Z a-z 0-9 . _ ~ + / = -"
    ]


@pytest.mark.parametrize(
    "bad", ['quo"te', "back\\slash", "a/b", "a#b", "a@b", "a%41", "pässwörd"]
)
@pytest.mark.parametrize("key", ["valkey_password", "superset_postgres_password"])
def test_url_embedded_values_are_unreserved(values, cluster_vars, key, bad):
    assert problems_for(dict(values, **{key: bad}), cluster_vars) == [
        "{}: may use only A-Z a-z 0-9 . _ ~ - (it goes into a connection URL)".format(
            key
        )
    ]


@pytest.mark.parametrize(
    "bad", ['quo"te', "back\\slash", "dollar${x}", "back`tick", "a,b", "a#b", "a@b"]
)
@pytest.mark.parametrize(
    "key", ["keycloak_temporal_client_secret", "s3_password", "trino_keystore_password"]
)
def test_every_value_uses_one_safe_set(values, cluster_vars, key, bad):
    assert problems_for(dict(values, **{key: bad + "-long-enough"}), cluster_vars) == [
        "{}: may use only A-Z a-z 0-9 . _ ~ + / = -".format(key)
    ]
    ok = base64.b64encode(b"\xfb\xff" * 12).decode()  # + / = all allowed
    assert problems_for(dict(values, **{key: ok}), cluster_vars) == []


def test_minio_minimum_lengths(values, cluster_vars):
    probs = problems_for(
        dict(values, s3_password="short", s3_lake_reader_secret="1234567"), cluster_vars
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
        ("0123456789abcdef0123456789abcdef", True),  # 32 (openssl rand -hex 16)
        (base64.urlsafe_b64encode(b"k" * 32).decode(), True),  # decodes to 32
        (base64.urlsafe_b64encode(b"k" * 32).decode().rstrip("="), True),  # unpadded
        (base64.b64encode(b"\xfb" * 32).decode(), False),  # + and / are not base64url
        ("0123456789abcdef0", False),  # 17 bytes, and not a valid encoding
    ],
)
def test_cookie_secret_aes_lengths(values, cluster_vars, cookie, ok):
    probs = problems_for(dict(values, oauth2_proxy_cookie_secret=cookie), cluster_vars)
    assert (probs == []) is ok


def test_placeholders_rejected_weak_defaults_warned(values, cluster_vars):
    probs, warns = validate(
        dict(
            values,
            keycloak_gh_client_id="your-github-client",
            postgres_password="$(openssl rand -hex 32 | ansible-vault encrypt_string --vault-password-file vault/pwd.sh)",
            trino_keystore_password="trinokeystorepass",
        ),
        cluster_vars,
        CONTRACT,
    )
    assert sorted(probs) == [
        "keycloak_gh_client_id: is an inventory.example.yaml placeholder",
        "postgres_password: is an inventory.example.yaml placeholder",
    ]
    assert warns == ["trino_keystore_password: is a weak Ansible default; rotate it"]


def test_cluster_var_layout_checks(values, cluster_vars):
    same_ns = dict(
        cluster_vars, hive_namespace=cluster_vars["postgres_cluster_namespace"]
    )
    assert [p.split(":")[0] for p in problems_for(values, same_ns)] == [
        "hive_namespace must differ from postgres_cluster_namespace"
    ]
    renamed = dict(
        cluster_vars,
        keycloak_postgres_user="kc",
        superset_postgres_user="superset",
        superset_database="superset_meta",
    )
    assert sorted(p.split(":")[0] for p in problems_for(values, renamed)) == [
        "keycloak_postgres_user",
        "superset_database",
    ]


def test_contract_rules_parse_fail_closed(tmp_path):
    bad = tmp_path / "contract.txt"
    bad.write_text("a\nb optional\nc when=flag_x\nd sometimes\n")
    with pytest.raises(ValueError, match="d: unknown rule sometimes"):
        load_contract(bad)


def test_main_fails_closed(tmp_path, values, capsys):
    out = tmp_path / "scout-secret-values.yaml"
    bad = tmp_path / "values.json"
    bad.write_text(json.dumps(dict(values, postgres_password="x'y")))
    with pytest.raises(SystemExit) as exc:
        main(
            [
                "--values",
                str(bad),
                "--cluster-vars-values",
                str(CLUSTER_VARS),
                "-o",
                str(out),
            ]
        )
    assert exc.value.code == 1
    assert "postgres_password: may use only" in capsys.readouterr().err
    assert not out.exists()

    # A hand-merged file with a key twice must not silently keep the last one.
    dup = tmp_path / "dup.json"
    dup.write_text(
        '{"postgres_password": "live-value-1", "postgres_password": "stale-2"}'
    )
    with pytest.raises(SystemExit):
        main(
            [
                "--values",
                str(dup),
                "--cluster-vars-values",
                str(CLUSTER_VARS),
                "-o",
                str(out),
            ]
        )
    err = capsys.readouterr().err
    assert "duplicate keys: postgres_password" in err and "live-value" not in err


def test_main_writes_owner_only(tmp_path, values):
    out = tmp_path / "scout-secret-values.yaml"
    out.write_text("stale")
    os.chmod(out, 0o200)  # pre-existing with another mode: the tool must set 0600
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
    assert os.stat(out).st_mode & 0o777 == 0o600
    assert yaml.safe_load(out.read_text())["stringData"] == values


def _vars(strings):
    """(names used, names used with a := default) across template strings."""
    found = [m for s in strings for m in VAR.findall(s) if m[0]]
    return {m[0] for m in found}, {m[0] for m in found if m[1]}


def test_contract_matches_the_templates():
    """required-secret-values.txt == the non-cluster-var names the templates use, its
    optional + conditional entries == the defaulted ones that aren't cluster-vars, and
    no name is in both contract lists (the later substituteFrom source would silently
    shadow the other)."""
    strings = [v for d in templates() for v in d["stringData"].values()]
    strings += [d["metadata"][k] for d in templates() for k in ("name", "namespace")]
    used, defaulted = _vars(strings)
    assert not set(CONTRACT) & REQUIRED_VARS
    assert used - REQUIRED_VARS - {"sq"} == set(CONTRACT)
    assert defaulted - REQUIRED_VARS == {
        k for k, rule in CONTRACT.items() if rule != "required"
    }
    flags = {
        rule[len("when=") :] for rule in CONTRACT.values() if rule.startswith("when=")
    }
    assert flags <= REQUIRED_VARS


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
