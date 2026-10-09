"""The absence check requires a successful Secret name listing."""

import subprocess

import pytest

import negative_cases


@pytest.mark.parametrize("stdout", ["", "secret/ci-neg-encrypted\n"])
def test_failed_secret_listing_cannot_prove_absence(monkeypatch, stdout):
    monkeypatch.setattr(
        negative_cases.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 1, stdout, "Unable to connect to the server: EOF"
        ),
    )

    with pytest.raises(SystemExit, match="Unable to connect to the server: EOF"):
        negative_cases.secrets_in("ci-negative")


@pytest.mark.parametrize(
    "stdout,names,expected",
    [
        ("", None, []),
        ("secret/ci-neg-encrypted\n", None, ["ci-neg-encrypted"]),
        (
            "secret/oauth2-proxy\nsecret/unrelated\n",
            {"oauth2-proxy", "oauth2-proxy-redis"},
            ["oauth2-proxy"],
        ),
    ],
)
def test_successful_secret_listing_returns_names_only(
    monkeypatch, stdout, names, expected
):
    def run(command, **kwargs):
        assert command == [
            "kubectl",
            "get",
            "secrets",
            "-n",
            "ci-negative",
            "-o",
            "name",
        ]
        return subprocess.CompletedProcess(command, 0, stdout, "")

    monkeypatch.setattr(negative_cases.subprocess, "run", run)

    assert negative_cases.secrets_in("ci-negative", names) == expected
