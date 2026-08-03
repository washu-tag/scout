"""Offline tests for the build_haul CLI glue. No network."""

import pytest

from build_haul import main, parse_fresh

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
