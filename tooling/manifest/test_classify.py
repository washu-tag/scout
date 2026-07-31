"""Offline truth-table tests for the build-lane change classifier. No network."""

import pytest

from classify import (
    SCOPE_ALL,
    SCOPE_CHANGED,
    SCOPE_SKIP,
    Classification,
    classify,
)

NAMES = ["hl7-transformer", "hl7-listener", "hl7log-extractor", "keycloak"]


def flags(**overrides):
    """A complete 'false' change map with the named ones flipped 'true'."""
    m = {n: "false" for n in NAMES}
    m.update({k: "true" for k in overrides})
    return m


def test_ci_forces_rebuild_all_even_with_nothing_changed():
    c = classify(NAMES, flags(), ci=True)
    assert c.scope == SCOPE_ALL
    assert c.rebuild == NAMES
    assert c.carry == []


def test_force_all_forces_rebuild_all():
    c = classify(NAMES, flags(), force_all=True)
    assert c == Classification(SCOPE_ALL, NAMES, [])


def test_ci_ignores_and_does_not_require_the_change_map():
    # A full rebuild does not read per-name flags, so an incomplete map is fine.
    c = classify(NAMES, {"keycloak": "true"}, ci=True)
    assert c.scope == SCOPE_ALL
    assert c.rebuild == NAMES


def test_nothing_changed_is_skip_carrying_everything():
    c = classify(NAMES, flags())
    assert c.scope == SCOPE_SKIP
    assert c.rebuild == []
    assert c.carry == NAMES


def test_changed_subset_partitions_in_name_order():
    changed = {
        "hl7-transformer": "false",
        "hl7-listener": "true",
        "hl7log-extractor": "false",
        "keycloak": "true",
    }
    c = classify(NAMES, changed)
    assert c.scope == SCOPE_CHANGED
    assert c.rebuild == ["hl7-listener", "keycloak"]  # order preserved
    assert c.carry == ["hl7-transformer", "hl7log-extractor"]  # order preserved


def test_accepts_bool_and_string_flags():
    changed = {
        "hl7-transformer": True,
        "hl7-listener": False,
        "hl7log-extractor": "TRUE ",  # whitespace + case tolerated
        "keycloak": "false",
    }
    c = classify(NAMES, changed)
    assert c.rebuild == ["hl7-transformer", "hl7log-extractor"]


def test_all_true_without_ci_is_changed_with_empty_carry():
    # A coincidental full rebuild is still rebuild-changed, not rebuild-all.
    c = classify(NAMES, {n: "true" for n in NAMES})
    assert c == Classification(SCOPE_CHANGED, NAMES, [])


def test_rebuild_scope_maps_to_wire_values():
    assert classify(NAMES, flags(), ci=True).rebuild_scope == "all"
    assert classify(NAMES, flags(keycloak="true")).rebuild_scope == "changed"
    assert classify(NAMES, flags()).rebuild_scope == "changed"  # skip -> changed


def test_missing_flag_fails_closed():
    partial = {"hl7-transformer": "true"}  # the other three have no flag
    with pytest.raises(ValueError, match="drifted from the changes job"):
        classify(NAMES, partial)


@pytest.mark.parametrize("bad", ["", None, "yes", "1", 1, 0, ["x"]])
def test_unrecognized_flag_fails_closed(bad):
    # A present-but-unusable flag (drift, incl. Actions' '' for a missing output)
    # must raise, not silently carry a stale digest.
    changed = flags()
    changed["keycloak"] = bad
    with pytest.raises(ValueError, match="unrecognized change flag"):
        classify(NAMES, changed)


def test_empty_names_rejected():
    with pytest.raises(ValueError, match="non-empty"):
        classify([], {})


def test_duplicate_names_rejected():
    with pytest.raises(ValueError, match="duplicate names"):
        classify(["a", "b", "a"], {"a": "true", "b": "false"})


def test_extra_flags_are_ignored():
    # Classifying images while the map also carries chart flags must not error.
    changed = flags()
    changed["some-chart"] = "true"
    c = classify(NAMES, changed)
    assert c.scope == SCOPE_SKIP
    assert "some-chart" not in c.rebuild
