"""The CI handoff must fail closed before exporting an artifact identity."""

import hashlib
import json

import pytest

from artifact_identity import (
    IdentityError,
    load_receipt,
    main,
    validate_manifest,
    validate_receipt,
)


CONTEXT = {
    "repository": "washu-tag/scout",
    "revision": "abcde" * 8,
    "run_id": 37378239488,
    "run_attempt": 2,
}


def digest(raw):
    return "sha256:" + hashlib.sha256(raw).hexdigest()


@pytest.fixture
def identity():
    receipt = {
        "schemaVersion": 1,
        "repository": CONTEXT["repository"],
        "revision": CONTEXT["revision"],
        "runId": CONTEXT["run_id"],
        "runAttempt": CONTEXT["run_attempt"],
        "version": "0.20261005.1234",
        "manifestDigest": "sha256:" + "a" * 64,
    }
    manifest = {
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "annotations": {
            "org.opencontainers.image.source": "https://github.com/washu-tag/scout",
            "org.opencontainers.image.revision": receipt["revision"],
            "org.opencontainers.image.version": receipt["version"],
            "io.scout.build.run-id": str(receipt["runId"]),
            "io.scout.build.run-attempt": str(receipt["runAttempt"]),
            "io.scout.build.manifest-digest": receipt["manifestDigest"],
        },
    }
    raw = json.dumps(manifest, separators=(",", ":")).encode()
    receipt["configDigest"] = digest(raw)
    return receipt, raw


def context_args(context=CONTEXT):
    return [
        "--repository",
        context["repository"],
        "--revision",
        context["revision"],
        "--run-id",
        str(context["run_id"]),
        "--run-attempt",
        str(context["run_attempt"]),
    ]


def test_create_validate_exports_only_three_safe_scalars(identity, tmp_path):
    receipt, raw = identity
    path, manifest, output = [
        tmp_path / name for name in ("receipt.json", "manifest.json", "out")
    ]
    assert (
        main(
            [
                "create",
                *context_args(),
                "--version",
                receipt["version"],
                "--manifest-digest",
                receipt["manifestDigest"],
                "--config-digest",
                receipt["configDigest"],
                "--output",
                str(path),
            ]
        )
        == 0
    )
    assert json.loads(path.read_text()) == receipt
    manifest.write_bytes(raw)
    output.write_text("previous=value\n")
    assert (
        main(
            [
                "validate",
                *context_args(),
                "--receipt",
                str(path),
                "--manifest",
                str(manifest),
                "--github-output",
                str(output),
            ]
        )
        == 0
    )
    assert output.read_text().splitlines() == [
        "previous=value",
        "version=" + receipt["version"],
        "config_digest=" + receipt["configDigest"],
        "manifest_digest=" + receipt["manifestDigest"],
    ]


@pytest.mark.parametrize(
    "field,value",
    [
        ("repository", "attacker/scout"),
        ("revision", "b" * 40),
        ("run_id", CONTEXT["run_id"] + 1),
        # GitHub re-runs retain their run ID: the previous attempt is stale.
        ("run_attempt", 3),
        ("run_attempt", True),
    ],
)
def test_receipt_must_match_trusted_workflow_context(identity, field, value):
    receipt, _ = identity
    with pytest.raises(IdentityError, match="context mismatch"):
        validate_receipt(receipt, **{**CONTEXT, field: value})


@pytest.mark.parametrize(
    "field,value",
    [
        ("schemaVersion", True),
        ("schemaVersion", 1.0),
        ("schemaVersion", 2),
        ("runId", True),
        ("runId", 0),
        ("runId", "37378239488"),
        ("runAttempt", False),
        ("runAttempt", -1),
        ("runAttempt", 2.0),
        ("repository", "https://github.com/washu-tag/scout"),
        ("repository", "washu-tag/scout/extra"),
        ("repository", "washu-tag/.."),
        ("revision", "a" * 39),
        ("revision", "A" * 40),
        ("version", "latest"),
        ("version", "0.20260230.1234"),
        ("version", "0.20261005.0"),
        ("version", "0.20261005.001"),
        ("configDigest", "sha256:" + "A" * 64),
        ("manifestDigest", "sha512:" + "a" * 64),
        ("configDigest", None),
    ],
)
def test_invalid_schema_values_rejected(identity, field, value):
    receipt, _ = identity
    receipt[field] = value
    with pytest.raises(IdentityError):
        validate_receipt(receipt, **CONTEXT)


def test_receipt_has_no_extra_registry_or_url_fields(identity):
    receipt, _ = identity
    for extra in ("configRepository", "url"):
        with pytest.raises(IdentityError, match="exactly the schema fields"):
            validate_receipt({**receipt, extra: "https://attacker.example"}, **CONTEXT)
    for field in receipt:
        with pytest.raises(IdentityError, match="exactly the schema fields"):
            validate_receipt(
                {k: v for k, v in receipt.items() if k != field}, **CONTEXT
            )


def test_local_repository_allowed_only_when_expected(identity):
    receipt, _ = identity
    receipt["repository"] = "local-owner/scout-ci_proof"
    validate_receipt(receipt, **{**CONTEXT, "repository": receipt["repository"]})
    with pytest.raises(IdentityError, match="context mismatch: repository"):
        validate_receipt(receipt, **CONTEXT)


@pytest.mark.parametrize(
    "field", ["repository", "revision", "version", "manifestDigest", "configDigest"]
)
def test_injection_never_reaches_github_output_or_logs(
    identity, tmp_path, capsys, field
):
    receipt, _ = identity
    receipt[field] += "\n::warning::injected\nconfig_digest=attacker"
    path, output = tmp_path / "receipt.json", tmp_path / "output"
    path.write_text(json.dumps(receipt))
    output.write_text("untouched=yes\n")
    assert (
        main(
            [
                "validate",
                *context_args(),
                "--receipt",
                str(path),
                "--github-output",
                str(output),
            ]
        )
        == 1
    )
    assert output.read_text() == "untouched=yes\n"
    captured = capsys.readouterr()
    assert "injected" not in captured.out + captured.err


@pytest.mark.parametrize(
    "raw",
    [
        b"null",
        b"[]",
        b"{",
        b"\xff",
    ],
)
def test_malformed_json_cannot_export(raw, tmp_path):
    path, output = tmp_path / "receipt.json", tmp_path / "output"
    path.write_bytes(raw)
    assert (
        main(
            [
                "validate",
                *context_args(),
                "--receipt",
                str(path),
                "--github-output",
                str(output),
            ]
        )
        == 1
    )
    assert not output.exists()


def test_manifest_hash_is_exact_bytes_not_reserialized_json(identity):
    receipt, raw = identity
    validate_manifest(raw, receipt)
    # Equivalent parsed JSON can be a different artifact. A tag may have moved
    # since publish, so matching annotations alone is insufficient.
    with pytest.raises(IdentityError, match="digest does not match"):
        validate_manifest(raw + b"\n", receipt)


@pytest.mark.parametrize(
    "annotation",
    [
        "org.opencontainers.image.source",
        "org.opencontainers.image.revision",
        "org.opencontainers.image.version",
        "io.scout.build.run-id",
        "io.scout.build.run-attempt",
        "io.scout.build.manifest-digest",
    ],
)
@pytest.mark.parametrize("change", ["tamper", "remove", "wrong_type"])
def test_every_oci_identity_annotation_is_bound(identity, annotation, change):
    receipt, raw = identity
    manifest = json.loads(raw)
    if change == "remove":
        del manifest["annotations"][annotation]
    else:
        manifest["annotations"][annotation] = (
            "different-build" if change == "tamper" else 2
        )
    raw = json.dumps(manifest).encode()
    receipt["configDigest"] = digest(raw)
    with pytest.raises(IdentityError, match="OCI annotation mismatch"):
        validate_manifest(raw, receipt)


def test_manifest_failure_emits_no_partial_outputs(identity, tmp_path):
    receipt, raw = identity
    path, manifest, output = [
        tmp_path / name for name in ("receipt.json", "manifest.json", "out")
    ]
    path.write_text(json.dumps(receipt))
    manifest.write_bytes(raw + b"\n")
    assert (
        main(
            [
                "validate",
                *context_args(),
                "--receipt",
                str(path),
                "--manifest",
                str(manifest),
                "--github-output",
                str(output),
            ]
        )
        == 1
    )
    assert not output.exists()


def test_create_rejects_invalid_identity_without_writing(identity, tmp_path):
    receipt, _ = identity
    path = tmp_path / "receipt.json"
    assert (
        main(
            [
                "create",
                *context_args(),
                "--version",
                "0.20261005.1\nevil=yes",
                "--manifest-digest",
                receipt["manifestDigest"],
                "--config-digest",
                receipt["configDigest"],
                "--output",
                str(path),
            ]
        )
        == 1
    )
    assert not path.exists()


@pytest.fixture
def release_identity(identity):
    receipt, raw = identity
    receipt.update(schemaVersion=2, bundleDigest="sha256:" + "b" * 64)
    return receipt, raw


def test_schema2_create_validate_exports_bound_bundle(release_identity, tmp_path):
    receipt, raw = release_identity
    path, manifest, output = [
        tmp_path / name for name in ("receipt", "manifest", "out")
    ]
    assert (
        main(
            [
                "create",
                *context_args(),
                "--version",
                receipt["version"],
                "--manifest-digest",
                receipt["manifestDigest"],
                "--bundle-digest",
                receipt["bundleDigest"],
                "--config-digest",
                receipt["configDigest"],
                "--output",
                str(path),
            ]
        )
        == 0
    )
    assert load_receipt(path) == receipt
    manifest.write_bytes(raw)
    assert (
        main(
            [
                "validate",
                *context_args(),
                "--receipt",
                str(path),
                "--manifest",
                str(manifest),
                "--require-bundle",
                "--github-output",
                str(output),
            ]
        )
        == 0
    )
    assert (
        output.read_text().splitlines()[-1]
        == "bundle_digest=" + receipt["bundleDigest"]
    )


def test_schema1_is_accepted_only_outside_release(identity, tmp_path):
    receipt, _ = identity
    validate_receipt(receipt, **CONTEXT)
    with pytest.raises(IdentityError, match="release requires"):
        validate_receipt(receipt, **CONTEXT, require_bundle=True)
    path, output = tmp_path / "receipt", tmp_path / "output"
    path.write_text(json.dumps(receipt))
    assert (
        main(
            [
                "validate",
                *context_args(),
                "--receipt",
                str(path),
                "--require-bundle",
                "--github-output",
                str(output),
            ]
        )
        == 1
    )
    assert not output.exists()


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "latest",
        "sha256:" + "A" * 64,
        True,
        "sha256:" + "a" * 64 + "\nevil=yes",
    ],
)
def test_schema2_rejects_invalid_bundle(release_identity, value):
    receipt, _ = release_identity
    receipt["bundleDigest"] = value
    with pytest.raises(IdentityError, match="invalid bundleDigest"):
        validate_receipt(receipt, **CONTEXT, require_bundle=True)


def test_config_digest_does_not_change_when_bundle_is_added(identity):
    receipt, raw = identity
    validate_manifest(raw, receipt)
    receipt.update(schemaVersion=2, bundleDigest="sha256:" + "b" * 64)
    validate_receipt(receipt, **CONTEXT, require_bundle=True)
    validate_manifest(raw, receipt)


@pytest.mark.parametrize("schema,has_bundle", [(1, True), (2, False), (3, True)])
def test_schema_and_field_set_must_agree(release_identity, schema, has_bundle):
    receipt, _ = release_identity
    receipt["schemaVersion"] = schema
    if not has_bundle:
        receipt.pop("bundleDigest")
    with pytest.raises(IdentityError):
        validate_receipt(receipt, **CONTEXT)
