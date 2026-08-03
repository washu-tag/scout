"""Offline tests for the build-lane digest-set resolver. No network."""

import pytest

from resolve import resolve_refs

D1 = "sha256:" + "1" * 64
D2 = "sha256:" + "2" * 64
COMPS = ["ghcr.io/washu-tag/hl7-listener", "ghcr.io/washu-tag/charts/hl7-listener"]


def _no_carry(repo):
    raise AssertionError(f"carry should not be called for {repo}")


def test_all_fresh_uses_fresh_tag_and_digest():
    fresh = {
        COMPS[0]: ("0.20260803.1", D1),
        COMPS[1]: ("0.20260803.1", D2),
    }
    refs = resolve_refs(COMPS, fresh=fresh, carry=_no_carry)
    assert refs == [f"{COMPS[0]}:0.20260803.1@{D1}", f"{COMPS[1]}:0.20260803.1@{D2}"]


def test_carry_supplies_unchanged_and_is_not_called_for_fresh():
    fresh = {COMPS[0]: ("0.20260803.1", D1)}  # only the image rebuilt
    calls = []

    def carry(repo):
        calls.append(repo)
        return ("0.20260729.5", D2)  # chart carried at its last-producing tag

    refs = resolve_refs(COMPS, fresh=fresh, carry=carry)
    assert refs[0] == f"{COMPS[0]}:0.20260803.1@{D1}"  # fresh
    assert refs[1] == f"{COMPS[1]}:0.20260729.5@{D2}"  # carried
    assert calls == [COMPS[1]]  # carry called only for the non-fresh one


def test_order_is_preserved():
    fresh = {c: ("0.20260803.1", D1) for c in COMPS}
    refs = resolve_refs(COMPS, fresh=fresh, carry=_no_carry)
    assert [r.split(":")[0] + ":" + r.split(":")[1].split("@")[0] for r in refs] == [
        f"{COMPS[0]}:0.20260803.1",
        f"{COMPS[1]}:0.20260803.1",
    ]


def test_missing_component_fails_closed():
    # not fresh, and carry can't supply it
    with pytest.raises(ValueError, match="neither rebuilt nor carried"):
        resolve_refs(COMPS, fresh={}, carry=lambda repo: None)


def test_invalid_digest_fails_closed():
    with pytest.raises(ValueError, match="invalid digest"):
        resolve_refs(
            COMPS, fresh={c: ("t", "not-a-digest") for c in COMPS}, carry=_no_carry
        )


def test_empty_tag_fails_closed():
    with pytest.raises(ValueError, match="empty tag"):
        resolve_refs(COMPS, fresh={c: ("", D1) for c in COMPS}, carry=_no_carry)


def test_empty_components_rejected():
    with pytest.raises(ValueError, match="at least one component"):
        resolve_refs([], fresh={}, carry=_no_carry)


def test_duplicate_components_rejected():
    with pytest.raises(ValueError, match="duplicate components"):
        resolve_refs(["a", "a"], fresh={"a": ("t", D1)}, carry=_no_carry)


def test_resolver_output_feeds_the_renderer():
    from haul import render_images

    fresh = {COMPS[0]: ("0.20260803.1", D1), COMPS[1]: ("0.20260803.1", D2)}
    manifest = render_images(resolve_refs(COMPS, fresh=fresh, carry=_no_carry))
    assert "kind: Images" in manifest
    assert f"@{D1}" in manifest and f"@{D2}" in manifest
