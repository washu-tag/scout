"""Offline tests for the build_haul CLI glue. No network."""

from urllib.error import HTTPError

import pytest

from build_haul import ghcr_digest, main, parse_fresh

D1 = "sha256:" + "1" * 64
D2 = "sha256:" + "2" * 64


def test_parse_fresh_reads_docker_push_artifact_files(tmp_path):
    (tmp_path / "hl7-listener.txt").write_text(
        f"hl7-listener ghcr.io/washu-tag/hl7-listener:0.20260803.1@{D1}\n"
    )
    (tmp_path / "keycloak.txt").write_text(
        f"keycloak ghcr.io/washu-tag/keycloak:0.20260803.1@{D2}\n"
    )
    fresh = parse_fresh(str(tmp_path))
    assert fresh["ghcr.io/washu-tag/hl7-listener"] == ("0.20260803.1", D1)
    assert fresh["ghcr.io/washu-tag/keycloak"] == ("0.20260803.1", D2)


def test_parse_fresh_rejects_unpinned_line(tmp_path):
    (tmp_path / "bad.txt").write_text(
        "hl7-listener ghcr.io/washu-tag/hl7-listener:0.1\n"
    )
    with pytest.raises(ValueError, match="unparseable digest line"):
        parse_fresh(str(tmp_path))


def test_main_renders_manifest_all_fresh(tmp_path, capsys):
    # every component is fresh, so no carry (no network) is exercised
    dd = tmp_path / "digests"
    dd.mkdir()
    (dd / "hl7-listener.txt").write_text(
        f"hl7-listener ghcr.io/washu-tag/hl7-listener:0.20260803.1@{D1}\n"
    )
    comps = tmp_path / "components.txt"
    comps.write_text("# platform\nghcr.io/washu-tag/hl7-listener\n")
    main(["--digests-dir", str(dd), "--components", str(comps), "--name", "scout"])
    out = capsys.readouterr().out
    assert "kind: Images" in out
    assert f"ghcr.io/washu-tag/hl7-listener:0.20260803.1@{D1}" in out


class _FakeResp:
    def __init__(self, body=b"", headers=None):
        self._body = body
        self.headers = headers or {}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_ghcr_digest_parses_content_digest_header():
    def opener(req):
        if isinstance(req, str):  # token endpoint
            return _FakeResp(body=b'{"token":"T"}')
        return _FakeResp(headers={"Docker-Content-Digest": D1})  # manifest HEAD

    assert ghcr_digest("ghcr.io/washu-tag/hl7-listener", "latest", opener=opener) == D1


def test_ghcr_digest_rejects_non_ghcr_repo():
    with pytest.raises(ValueError, match="ghcr.io/<path>"):
        ghcr_digest("docker.io/library/alpine", "latest", opener=lambda *a: None)


def test_ghcr_digest_authed_uses_base64_bearer_and_skips_token_endpoint():
    import base64

    seen = []

    def opener(req):
        seen.append(req)
        assert not isinstance(req, str), "authed path must not hit the token endpoint"
        return _FakeResp(headers={"Docker-Content-Digest": D1})

    got = ghcr_digest(
        "ghcr.io/washu-tag/charts/orthanc",
        "latest",
        github_token="ghs_secret",
        opener=opener,
    )
    assert got == D1
    assert len(seen) == 1  # single HEAD, no separate anonymous token fetch
    expected = "Bearer " + base64.b64encode(b"ghs_secret").decode()
    assert seen[0].headers["Authorization"] == expected


def _carry_only(tmp_path):
    # empty digests dir -> nothing fresh -> every component is carried
    dd = tmp_path / "digests"
    dd.mkdir()
    comps = tmp_path / "components.txt"
    comps.write_text("ghcr.io/washu-tag/hl7-listener\n")
    return ["--digests-dir", str(dd), "--components", str(comps)]


def test_carry_missing_tag_404_fails_closed(tmp_path, monkeypatch):
    def boom(repo, tag, **_):
        raise HTTPError("https://ghcr.io/v2/...", 404, "Not Found", None, None)

    monkeypatch.setattr("build_haul.ghcr_digest", boom)
    with pytest.raises(ValueError, match="neither rebuilt nor carried"):
        main(_carry_only(tmp_path))


def test_carry_registry_error_is_contextual(tmp_path, monkeypatch):
    def boom(repo, tag, **_):
        raise HTTPError("https://ghcr.io/v2/...", 500, "Server Error", None, None)

    monkeypatch.setattr("build_haul.ghcr_digest", boom)
    with pytest.raises(RuntimeError, match="registry error carrying"):
        main(_carry_only(tmp_path))
