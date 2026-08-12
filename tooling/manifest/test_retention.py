"""Offline tests for the haul retention policy (tooling/manifest/retention.py)."""

import pytest

from retention import partition, versions_from_api

H = {c: c * 64 for c in "abcdef"}  # 64-hex digests keyed by a letter


def _v(vid, created, tags, digest_letter):
    return {
        "id": vid,
        "created_at": created,
        "tags": tags,
        "digest": "sha256:" + H[digest_letter],
    }


def _artifact(vid, created, version_tag, digest_letter, main=False):
    tags = [version_tag] + (["main"] if main else [])
    return _v(vid, created, tags, digest_letter)


def _sig(vid, created, subject_letter, own_letter):
    return _v(vid, created, ["sha256-" + H[subject_letter] + ".sig"], own_letter)


def test_keeps_newest_n_deletes_older():
    versions = [
        _artifact(1, "2026-08-01T00:00:00Z", "0.20260801.1", "a"),
        _artifact(2, "2026-08-02T00:00:00Z", "0.20260802.1", "b"),
        _artifact(3, "2026-08-03T00:00:00Z", "0.20260803.1", "c", main=True),
    ]
    keep, delete = partition(versions, keep_n=2)
    assert keep == {2, 3} and delete == {1}


def test_main_is_kept_even_when_older_than_keep_n():
    # main pinned to an OLD artifact (unusual, but must never be pruned)
    versions = [
        _artifact(1, "2026-08-01T00:00:00Z", "0.20260801.1", "a", main=True),
        _artifact(2, "2026-08-02T00:00:00Z", "0.20260802.1", "b"),
        _artifact(3, "2026-08-03T00:00:00Z", "0.20260803.1", "c"),
    ]
    keep, delete = partition(versions, keep_n=1)
    assert 1 in keep and 3 in keep  # main + newest
    assert delete == {2}


def test_signature_follows_its_bundle():
    versions = [
        _artifact(
            1, "2026-08-01T00:00:00Z", "0.20260801.1", "a"
        ),  # old bundle -> delete
        _sig(
            2, "2026-08-01T00:01:00Z", subject_letter="a", own_letter="d"
        ),  # its sig -> delete
        _artifact(
            3, "2026-08-03T00:00:00Z", "0.20260803.1", "b", main=True
        ),  # kept bundle
        _sig(
            4, "2026-08-03T00:01:00Z", subject_letter="b", own_letter="e"
        ),  # its sig -> keep
    ]
    keep, delete = partition(versions, keep_n=1)
    assert keep == {3, 4} and delete == {1, 2}


def test_untagged_and_orphan_sig_are_kept_conservatively():
    versions = [
        _artifact(1, "2026-08-03T00:00:00Z", "0.20260803.1", "a", main=True),
        {
            "id": 2,
            "created_at": "2026-08-03T00:02:00Z",
            "tags": [],
            "digest": "sha256:" + H["b"],
        },  # untagged
        _sig(
            3, "2026-08-01T00:00:00Z", subject_letter="f", own_letter="c"
        ),  # subject absent -> orphan
    ]
    keep, delete = partition(versions, keep_n=1)
    assert keep == {1, 2, 3} and delete == set()


def test_fewer_than_keep_n_keeps_all():
    versions = [
        _artifact(1, "2026-08-01T00:00:00Z", "0.20260801.1", "a"),
        _artifact(2, "2026-08-02T00:00:00Z", "0.20260802.1", "b", main=True),
    ]
    keep, delete = partition(versions, keep_n=10)
    assert keep == {1, 2} and delete == set()


def test_empty():
    assert partition([], keep_n=5) == (set(), set())


def test_keep_n_must_be_positive():
    with pytest.raises(ValueError):
        partition([], keep_n=0)


def test_versions_from_api_shape_and_partition():
    # Real `GET .../versions` shape: digest in `name`, tags under metadata.container.
    api = [
        {
            "id": 10,
            "created_at": "2026-08-01T00:00:00Z",
            "name": "sha256:" + H["a"],
            "metadata": {"container": {"tags": ["0.20260801.1"]}},
        },
        {
            "id": 11,
            "created_at": "2026-08-03T00:00:00Z",
            "name": "sha256:" + H["b"],
            "metadata": {"container": {"tags": ["0.20260803.1", "main"]}},
        },
        {
            "id": 12,
            "created_at": "2026-08-01T00:01:00Z",
            "name": "sha256:" + H["c"],
            "metadata": {"container": {"tags": ["sha256-" + H["a"] + ".sig"]}},
        },
        {
            "id": 13,
            "created_at": "2026-08-03T00:02:00Z",
            "name": "sha256:" + H["d"],
            "metadata": {},
        },  # untagged / no container metadata
    ]
    versions = versions_from_api(api)
    assert versions[0]["digest"] == "sha256:" + H["a"]
    assert versions[1]["tags"] == ["0.20260803.1", "main"]
    assert versions[3]["tags"] == []
    keep, delete = partition(versions, keep_n=1)
    assert keep == {11, 13} and delete == {10, 12}
