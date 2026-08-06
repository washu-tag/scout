"""Offline tests for the build_haul CLI glue. No network."""

import pytest

from build_haul import main, parse_fresh, parse_predecessor
from haul import render_images

D1 = "sha256:" + "1" * 64
D2 = "sha256:" + "2" * 64

IMG = "ghcr.io/washu-tag/hl7-listener"
KC = "ghcr.io/washu-tag/keycloak"


def test_parse_fresh_reads_docker_push_artifact_files(tmp_path):
    (tmp_path / "hl7-listener.txt").write_text(
        f"hl7-listener {IMG}:0.20260803.1@{D1}\n"
    )
    (tmp_path / "keycloak.txt").write_text(f"keycloak {KC}:26.6.3@{D2}\n")
    fresh = parse_fresh(str(tmp_path))
    assert fresh[IMG] == ("0.20260803.1", D1)
    assert fresh[KC] == ("26.6.3", D2)


def test_parse_fresh_rejects_unpinned_line(tmp_path):
    (tmp_path / "bad.txt").write_text(f"hl7-listener {IMG}:0.1\n")
    with pytest.raises(ValueError, match="unparseable digest line"):
        parse_fresh(str(tmp_path))


def test_main_renders_manifest_all_fresh(tmp_path, capsys):
    # every component is fresh, so the predecessor is never consulted
    dd = tmp_path / "digests"
    dd.mkdir()
    (dd / "hl7-listener.txt").write_text(f"hl7-listener {IMG}:0.20260803.1@{D1}\n")
    comps = tmp_path / "components.txt"
    comps.write_text(f"# platform\n{IMG}\n")
    main(["--digests-dir", str(dd), "--components", str(comps), "--name", "scout"])
    out = capsys.readouterr().out
    assert "kind: Images" in out
    assert f"{IMG}:0.20260803.1@{D1}" in out


def test_parse_predecessor_reads_manifest(tmp_path):
    prev = tmp_path / "prev.yaml"
    prev.write_text(
        render_images([f"{IMG}:0.old@{D1}", f"{KC}:26.6.3@{D2}"], name="scout")
    )
    got = parse_predecessor(str(prev))
    assert got == {IMG: ("0.old", D1), KC: ("26.6.3", D2)}


def test_parse_predecessor_missing_or_empty_returns_empty(tmp_path):
    assert parse_predecessor("") == {}
    assert parse_predecessor(str(tmp_path / "nope.yaml")) == {}
    empty = tmp_path / "empty.yaml"
    empty.write_text("   \n")
    assert parse_predecessor(str(empty)) == {}


def test_parse_predecessor_rejects_bad_ref(tmp_path):
    prev = tmp_path / "prev.yaml"
    prev.write_text(
        "spec:\n  images:\n    - name: ghcr.io/washu-tag/x:0.1\n"
    )  # no digest
    with pytest.raises(ValueError, match="unparseable predecessor ref"):
        parse_predecessor(str(prev))


def test_render_roundtrips_through_predecessor_parse(tmp_path):
    # what this run publishes must parse back to the same carry map next run
    refs = [f"{IMG}:0.20260803.1@{D1}", f"{KC}:26.6.3@{D2}"]
    prev = tmp_path / "haul.yaml"
    prev.write_text(render_images(refs, name="scout"))
    assert parse_predecessor(str(prev)) == {
        IMG: ("0.20260803.1", D1),
        KC: ("26.6.3", D2),
    }


def _carry_setup(tmp_path):
    # hl7-listener rebuilt this run; keycloak unchanged -> carried from predecessor
    dd = tmp_path / "digests"
    dd.mkdir()
    (dd / "hl7-listener.txt").write_text(f"hl7-listener {IMG}:0.20260803.9@{D1}\n")
    comps = tmp_path / "components.txt"
    comps.write_text(f"{IMG}\n{KC}\n")
    return dd, comps


def test_main_carries_unchanged_from_predecessor(tmp_path, capsys):
    dd, comps = _carry_setup(tmp_path)
    prev = tmp_path / "prev.yaml"
    prev.write_text(
        render_images([f"{IMG}:0.old@{D2}", f"{KC}:26.6.3@{D2}"], name="scout")
    )
    main(
        [
            "--digests-dir",
            str(dd),
            "--components",
            str(comps),
            "--predecessor",
            str(prev),
        ]
    )
    out = capsys.readouterr().out
    assert f"{IMG}:0.20260803.9@{D1}" in out  # fresh wins over predecessor
    assert f"{KC}:26.6.3@{D2}" in out  # carried from predecessor


def test_main_fails_closed_when_neither_fresh_nor_in_predecessor(tmp_path):
    dd, comps = _carry_setup(tmp_path)  # keycloak not fresh
    prev = tmp_path / "prev.yaml"
    prev.write_text(render_images([f"{IMG}:0.old@{D2}"], name="scout"))  # no keycloak
    with pytest.raises(ValueError, match="neither rebuilt nor carried"):
        main(
            [
                "--digests-dir",
                str(dd),
                "--components",
                str(comps),
                "--predecessor",
                str(prev),
            ]
        )
