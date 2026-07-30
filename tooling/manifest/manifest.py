"""Scout build manifest (ADR 0030 build lane).

A build manifest is the authoritative record of the whole Scout platform for one
build- or release-lane version: every image and chart pinned by ref + digest,
the source commit it describes, and a link to its predecessor. Deployments
(ADR 0031) resolve components from it, so content that did not change this build
carries the SAME digest forward and its pods do not restart.

This module is dependency-free (stdlib only) so it runs on a CI runner with no
install step. ``schema.json`` beside it is the canonical wire contract;
``validate(...)`` checks a manifest against it and imports ``jsonschema`` lazily
(dev/test only).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Optional

SCHEMA_VERSION = 1
MEDIA_TYPE = "application/vnd.scout.build-manifest.v1+json"
SCHEMA_PATH = Path(__file__).with_name("schema.json")


@dataclass
class Artifact:
    """One image or chart, pinned immutably by ``ref@digest``."""

    name: str
    ref: str  # e.g. ghcr.io/washu-tag/hl7-transformer:0.20260730.412
    digest: str  # sha256:...
    producedByBuild: str  # build version whose run produced this content
    changedThisBuild: bool
    appVersion: Optional[str] = None  # charts only (Phase 2 PR5); None for images
    primaryImage: Optional[str] = None  # charts only (Phase 2 PR5)

    def pinned(self) -> str:
        """The immutable ``ref@digest`` a deployment should pull."""
        return f"{self.ref}@{self.digest}"


@dataclass
class Predecessor:
    version: str
    sourceCommit: str
    digest: Optional[str] = None


@dataclass
class Manifest:
    version: str
    sourceCommit: str
    rebuildScope: str  # "changed" | "all"
    images: list[Artifact] = field(default_factory=list)
    charts: list[Artifact] = field(default_factory=list)
    predecessor: Optional[Predecessor] = None
    kind: str = "scout.build"  # "scout.build" | "scout.release"
    schemaVersion: int = SCHEMA_VERSION
    configArtifact: Optional[dict] = None  # ADR 0031 (Phase 3); null here
    bom: Optional[list] = None  # ADR 0031 (Phase 3); null here

    def resolve(self, name: str) -> str:
        """Return the pinned ``ref@digest`` for image or chart ``name``."""
        for a in (*self.images, *self.charts):
            if a.name == name:
                return a.pinned()
        raise KeyError(f"{name!r} is not in build manifest {self.version}")

    def to_dict(self) -> dict:
        return {
            "schemaVersion": self.schemaVersion,
            "kind": self.kind,
            "version": self.version,
            "sourceCommit": self.sourceCommit,
            "rebuildScope": self.rebuildScope,
            "predecessor": _pred_dict(self.predecessor),
            "images": [_artifact_dict(a) for a in self.images],
            "charts": [_artifact_dict(a) for a in self.charts],
            "configArtifact": self.configArtifact,
            "bom": self.bom,
        }

    def dumps(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent) + "\n"

    @classmethod
    def from_dict(cls, d: dict) -> "Manifest":
        pred = d.get("predecessor")
        return cls(
            version=d["version"],
            sourceCommit=d["sourceCommit"],
            rebuildScope=d["rebuildScope"],
            images=[_artifact_from(a) for a in d.get("images", [])],
            charts=[_artifact_from(a) for a in d.get("charts", [])],
            predecessor=Predecessor(**pred) if pred else None,
            kind=d.get("kind", "scout.build"),
            schemaVersion=d.get("schemaVersion", SCHEMA_VERSION),
            configArtifact=d.get("configArtifact"),
            bom=d.get("bom"),
        )

    @classmethod
    def loads(cls, s: str) -> "Manifest":
        return cls.from_dict(json.loads(s))


def carry_section(
    previous: list[Artifact],
    fresh: list[Artifact],
    all_names: list[str],
    *,
    build_version: str,
) -> list[Artifact]:
    """Assemble one section (images or charts) for a new manifest.

    For each name in ``all_names``: if it was rebuilt this run it is taken from
    ``fresh`` (stamped ``changedThisBuild=True`` and ``producedByBuild`` = this
    build); otherwise it is carried from ``previous`` unchanged
    (``changedThisBuild=False``, digest preserved).

    Fail closed: a name that is neither fresh nor present in ``previous`` raises,
    the caller must rebuild it rather than publish a manifest with a hole.
    """
    fresh_by = {a.name: a for a in fresh}
    prev_by = {a.name: a for a in previous}
    out: list[Artifact] = []
    for name in all_names:
        if name in fresh_by:
            out.append(
                replace(
                    fresh_by[name], changedThisBuild=True, producedByBuild=build_version
                )
            )
        elif name in prev_by:
            out.append(replace(prev_by[name], changedThisBuild=False))
        else:
            raise ValueError(
                f"{name!r} did not change this build but is absent from the "
                f"predecessor manifest; rebuild it instead of shipping a hole"
            )
    return out


def assemble(
    *,
    version: str,
    source_commit: str,
    rebuild_scope: str,
    previous: Optional[Manifest],
    fresh_images: list[Artifact],
    fresh_charts: list[Artifact],
    all_image_names: list[str],
    all_chart_names: list[str],
    kind: str = "scout.build",
) -> Manifest:
    """Build a full manifest, carrying unchanged components from ``previous``."""
    prev_images = previous.images if previous else []
    prev_charts = previous.charts if previous else []
    predecessor = (
        Predecessor(version=previous.version, sourceCommit=previous.sourceCommit)
        if previous
        else None
    )
    return Manifest(
        version=version,
        sourceCommit=source_commit,
        rebuildScope=rebuild_scope,
        images=carry_section(
            prev_images, fresh_images, all_image_names, build_version=version
        ),
        charts=carry_section(
            prev_charts, fresh_charts, all_chart_names, build_version=version
        ),
        predecessor=predecessor,
        kind=kind,
    )


def validate(manifest_dict: dict) -> None:
    """Validate a manifest dict against schema.json. Raises on violation.

    Imports ``jsonschema`` lazily so the read/write/assemble paths stay
    dependency-free on a CI runner.
    """
    import jsonschema  # noqa: PLC0415  (lazy: dev/test-only dependency)

    schema = json.loads(SCHEMA_PATH.read_text())
    jsonschema.validate(instance=manifest_dict, schema=schema)


def _artifact_dict(a: Artifact) -> dict:
    d = {
        "name": a.name,
        "ref": a.ref,
        "digest": a.digest,
        "producedByBuild": a.producedByBuild,
        "changedThisBuild": a.changedThisBuild,
    }
    if a.appVersion is not None:
        d["appVersion"] = a.appVersion
    if a.primaryImage is not None:
        d["primaryImage"] = a.primaryImage
    return d


def _artifact_from(d: dict) -> Artifact:
    return Artifact(
        name=d["name"],
        ref=d["ref"],
        digest=d["digest"],
        producedByBuild=d["producedByBuild"],
        changedThisBuild=d["changedThisBuild"],
        appVersion=d.get("appVersion"),
        primaryImage=d.get("primaryImage"),
    )


def _pred_dict(p: Optional[Predecessor]) -> Optional[dict]:
    if p is None:
        return None
    return {"version": p.version, "sourceCommit": p.sourceCommit, "digest": p.digest}
