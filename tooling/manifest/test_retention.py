"""Offline tests for the haul retention policy (tooling/manifest/retention.py)."""

from datetime import datetime, timedelta, timezone

import pytest

from retention import partition, versions_from_api

H = {c: c * 64 for c in "abcde"}  # 64-hex digests keyed by a letter
NOW = datetime(2026, 8, 15, tzinfo=timezone.utc)


def _ago(days):
    return (NOW - timedelta(days=days)).isoformat()


def _artifact(vid, days_ago, version_tag, digest_letter, main=False):
    tags = [version_tag] + (["main"] if main else [])
    return {
        "id": vid,
        "created_at": _ago(days_ago),
        "tags": tags,
        "digest": "sha256:" + H[digest_letter],
    }


def _sig(vid, days_ago, subject_letter, own_letter):
    return {
        "id": vid,
        "created_at": _ago(days_ago),
        "tags": ["sha256-" + H[subject_letter] + ".sig"],
        "digest": "sha256:" + H[own_letter],
    }


def test_keeps_young_deletes_old():
    versions = [
        _artifact(1, 40, "0.a", "a"),  # old -> delete
        _artifact(2, 20, "0.b", "b"),  # old -> delete
        _artifact(3, 3, "0.c", "c"),  # young -> keep
        _artifact(4, 1, "0.d", "d", main=True),  # young + main -> keep
    ]
    keep, delete = partition(versions, keep_days=7, min_keep=1, now=NOW)
    assert keep == {3, 4} and delete == {1, 2}


def test_min_keep_protects_a_quiet_period():
    # everything older than keep_days, but min_keep retains the newest two
    versions = [
        _artifact(1, 100, "0.a", "a"),
        _artifact(2, 90, "0.b", "b"),
        _artifact(3, 80, "0.c", "c"),
    ]
    keep, delete = partition(versions, keep_days=7, min_keep=2, now=NOW)
    assert keep == {2, 3} and delete == {1}


def test_main_kept_regardless_of_age():
    versions = [
        _artifact(1, 200, "0.a", "a", main=True),  # ancient but main
        _artifact(2, 3, "0.b", "b"),
    ]
    keep, delete = partition(versions, keep_days=7, min_keep=1, now=NOW)
    assert keep == {1, 2} and delete == set()


def test_release_tag_kept_but_build_tag_ages_out():
    # A release haul (X.Y.Z, X>=1) is pinned forever; a same-age build haul
    # (leading-0) is not, so retention still bounds the build lane.
    versions = [
        _artifact(1, 300, "4.2.0", "a"),  # ancient release -> keep
        _artifact(2, 300, "0.20260812.1234", "b"),  # ancient build -> delete
        _artifact(3, 1, "0.20260815.5", "c", main=True),  # newest build, main
    ]
    keep, delete = partition(versions, keep_days=7, min_keep=1, now=NOW)
    assert keep == {1, 3} and delete == {2}


def test_release_signature_kept_with_its_bundle():
    # id 5 is the newest, so min_keep=1 protects it (not the old build id 3).
    versions = [
        _artifact(1, 300, "4.2.0", "a"),  # ancient release bundle -> keep (pinned)
        _sig(2, 300, subject_letter="a", own_letter="d"),  # its sig -> keep too
        _artifact(3, 300, "0.20260812.1234", "b"),  # ancient build bundle -> delete
        _sig(4, 300, subject_letter="b", own_letter="e"),  # its sig -> delete
        _artifact(5, 1, "0.20260815.9", "c", main=True),  # newest build -> keep
    ]
    keep, delete = partition(versions, keep_days=7, min_keep=1, now=NOW)
    assert keep == {1, 2, 5} and delete == {3, 4}


def test_signature_follows_its_bundle():
    versions = [
        _artifact(1, 40, "0.a", "a"),  # old bundle -> delete
        _sig(2, 40, subject_letter="a", own_letter="d"),  # its sig -> delete
        _artifact(3, 1, "0.b", "b", main=True),  # kept bundle
        _sig(4, 1, subject_letter="b", own_letter="e"),  # its sig -> keep
    ]
    keep, delete = partition(versions, keep_days=7, min_keep=1, now=NOW)
    assert keep == {3, 4} and delete == {1, 2}


def test_untagged_kept():
    versions = [
        _artifact(1, 1, "0.a", "a", main=True),
        {
            "id": 2,
            "created_at": _ago(200),
            "tags": [],
            "digest": "sha256:" + H["b"],
        },  # untagged
    ]
    keep, delete = partition(versions, keep_days=7, min_keep=1, now=NOW)
    assert keep == {1, 2} and delete == set()


def test_empty():
    assert partition([], keep_days=30, min_keep=5, now=NOW) == (set(), set())


def test_min_keep_must_be_positive():
    with pytest.raises(ValueError):
        partition([], keep_days=30, min_keep=0, now=NOW)


def test_keep_days_must_be_nonneg():
    with pytest.raises(ValueError):
        partition([], keep_days=-1, min_keep=5, now=NOW)


def test_versions_from_api_shape_and_partition():
    # Real `GET .../versions` shape (Z timestamps): digest in `name`, tags under
    # metadata.container. NOW=2026-08-15, keep_days=7 -> cutoff 2026-08-08.
    api = [
        {
            "id": 10,
            "created_at": "2026-06-01T00:00:00Z",
            "name": "sha256:" + H["a"],
            "metadata": {"container": {"tags": ["0.a"]}},
        },
        {
            "id": 11,
            "created_at": "2026-08-14T00:00:00Z",
            "name": "sha256:" + H["b"],
            "metadata": {"container": {"tags": ["0.b", "main"]}},
        },
        {
            "id": 12,
            "created_at": "2026-06-01T00:00:00Z",
            "name": "sha256:" + H["c"],
            "metadata": {"container": {"tags": ["sha256-" + H["a"] + ".sig"]}},
        },
        {
            "id": 13,
            "created_at": "2026-08-14T00:00:00Z",
            "name": "sha256:" + H["d"],
            "metadata": {},
        },
    ]
    versions = versions_from_api(api)
    assert versions[0]["digest"] == "sha256:" + H["a"]
    assert versions[1]["tags"] == ["0.b", "main"]
    assert versions[3]["tags"] == []
    keep, delete = partition(versions, keep_days=7, min_keep=1, now=NOW)
    assert keep == {11, 13} and delete == {10, 12}
