"""Reading fragments off disk, with provenance.

The reconciler never talks to the Kubernetes API to find fragments; a kiwigrid
k8s-sidecar writes discovered ConfigMaps into a shared emptyDir and we read
files. That keeps the watch logic out of our code and, more importantly, keeps
the reconciler's own API permissions small.

Provenance survives the trip because the sidecar runs with UNIQUE_FILENAMES,
which encodes namespace and resource name into the filename. That pair is the
only provenance worth reporting: it is attested by the cluster, unlike anything
a fragment says about itself.
"""

import hashlib
import json
import logging
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from . import yamlio

from .schema import FRAGMENT_LABEL, FRAGMENT_LABEL_VALUE, KIND, Fragment

# kiwigrid k8s-sidecar, UNIQUE_FILENAMES=true:
#   namespace_<namespace>.<resource>_<name>.<data-key>
# Verified against a running launchpad catalog sidecar. The namespace is a DNS
# label and cannot hold a dot; everything after the resource is the name and
# the key, and `parse_source_name` decides whether that split is knowable.
SIDECAR_FILENAME_RE = re.compile(
    r"^namespace_(?P<namespace>[^.]+)\.(?P<resource>[a-z]+)_(?P<rest>.+)$"
)

YAML_SUFFIXES = (".yaml", ".yml", ".json")

# A fragment ConfigMap's data key carries one dot, its suffix. That is what
# makes the sidecar's filename splittable: the name may hold dots, the key may
# not, so the last two segments are always the key. `_from_configmap` enforces
# it where a real ConfigMap is in hand, which is before anything is deployed.
DATA_KEY_RULE = (
    "a fragment's data key must be <name>.yaml, .yml or .json, with no other dot"
)


log = logging.getLogger(__name__)


def parse_realm_document(text: str) -> dict:
    """Parse the published base realm, tolerating trailing commas.

    A cluster can be holding a realm whose `identityProviders` array ends `,]`,
    published by a deploy that rendered one comma per conditional block.
    keycloak-config-cli's Jackson parser accepts that and imports it happily;
    Python's json module does not, and refusing it here would mean the
    reconciler cannot start on a cluster that is otherwise working.
    """
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        repaired = _strip_trailing_commas(text)
        parsed = json.loads(repaired)
        log.warning(
            "base realm is not strict JSON (trailing comma before a closing "
            "bracket); parsed after repair. Re-run the keycloak role, or "
            "reconcile the realm HelmRelease, to publish it again."
        )
        return parsed


def _strip_trailing_commas(text: str) -> str:
    """Drop `,` that sits immediately before `]` or `}`, ignoring strings."""
    out: list[str] = []
    in_string = False
    escaped = False
    length = len(text)
    for index, char in enumerate(text):
        if in_string:
            out.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            out.append(char)
            continue
        if char == ",":
            lookahead = index + 1
            while lookahead < length and text[lookahead] in " \t\r\n":
                lookahead += 1
            if lookahead < length and text[lookahead] in "]}":
                continue
        out.append(char)
    return "".join(out)


@dataclass(frozen=True, order=True)
class FragmentRef:
    namespace: str
    name: str

    def __str__(self) -> str:
        return f"{self.namespace}/{self.name}"


@dataclass
class LoadedFragment:
    """One discovered fragment: every document from one ConfigMap."""

    ref: FragmentRef
    fragment: Fragment | None
    content_hash: str
    errors: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return self.fragment is not None and not self.errors


def parse_source_name(filename: str) -> tuple[FragmentRef, str] | None:
    """The ConfigMap a sidecar-written file came from, and the data key in it.

    Split from the right, because a ConfigMap name is a DNS *subdomain* and may
    hold dots while a fragment's data key may not (see `DATA_KEY_RULE`). So
    `namespace_kc.configmap_app.blue.fragment.yaml` is `app.blue` holding
    `fragment.yaml`, and its sibling `app.green` is a different ConfigMap.

    Splitting from the left instead reads both as `app`: two components'
    clients merge into one fragment under one ref, and that ref is written into
    every client's `scout.fragment.source` attribute as provenance the cluster
    is supposed to have attested.

    None when the filename is not the sidecar's shape at all, which is a
    hand-placed file and genuinely has no provenance.
    """
    match = SIDECAR_FILENAME_RE.match(filename)
    if not match:
        return None
    if match.group("resource") not in ("configmap", "secret"):
        return None
    rest = match.group("rest")
    suffix = _suffix_of(rest)
    if not suffix:
        return None
    head, dot, tail = rest[: -len(suffix)].rpartition(".")
    name, key = (head, tail + suffix) if dot else (tail, suffix.lstrip("."))
    if not name or not key:
        return None
    return FragmentRef(match.group("namespace"), name), key


def scan(directory: str | Path) -> list[LoadedFragment]:
    """Load every fragment in `directory`, grouped by source ConfigMap.

    Never raises. A file that cannot be read, parsed, or validated becomes a
    LoadedFragment carrying errors — one bad fragment must not stop the others
    from being composed.
    """
    root = Path(directory)
    grouped: dict[FragmentRef, list[tuple[str, str, str | None]]] = {}

    try:
        entries = sorted(p for p in root.iterdir() if p.is_file())
    except OSError as exc:
        return [
            LoadedFragment(
                ref=FragmentRef("-", str(root)),
                fragment=None,
                content_hash="",
                errors=[f"cannot read fragment directory: {exc}"],
            )
        ]

    for path in entries:
        if not path.name.lower().endswith(YAML_SUFFIXES):
            continue
        parsed = parse_source_name(path.name)
        if parsed is None:
            # A hand-placed file, which is the CLI's normal case and has no
            # cluster provenance by nature.
            ref, key = FragmentRef("-", path.name), path.name
        else:
            ref, key = parsed
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            grouped.setdefault(ref, []).append((key, "", f"cannot read: {exc}"))
            continue
        grouped.setdefault(ref, []).append((key, text, None))

    return [_assemble(ref, docs) for ref, docs in sorted(grouped.items())]


def scan_stream(text: str, source: str = "stdin") -> list[LoadedFragment]:
    """Load fragments from what an author has before anything is deployed: a
    rendered chart, a single ConfigMap, or a bare fragment document.

    `scan()` reads what the sidecar wrote. This reaches that same code by
    writing what it finds into a temporary directory under the sidecar's
    filename convention, so an offline verdict is the in-cluster verdict down
    to the provenance and the content hash. Never raises, for scan()'s reason.
    """
    try:
        documents = [d for d in yamlio.safe_load_all(text) if isinstance(d, dict)]
    except yamlio.YAMLError as exc:
        return [_problem(FragmentRef("-", source), f"not valid YAML: {_one_line(exc)}")]

    files: dict[str, str] = {}
    problems: list[LoadedFragment] = []
    for index, document in enumerate(documents):
        if document.get("kind") == KIND:
            # Verbatim when it is the whole input, so the hash matches the file
            # the author is looking at.
            body = text if len(documents) == 1 else yamlio.safe_dump(document)
            files[_bare_name(source, index if len(documents) > 1 else None)] = body
        elif document.get("kind") == "ConfigMap":
            _from_configmap(document, files, problems)

    if not files:
        return problems
    try:
        with tempfile.TemporaryDirectory() as tmp:
            for name, body in files.items():
                (Path(tmp) / name).write_text(body, encoding="utf-8")
            return sorted(scan(tmp) + problems, key=lambda item: item.ref)
    except OSError as exc:
        # The container runs with a read-only root filesystem, so this is a
        # missing writable /tmp rather than a bad fragment. It is still not
        # allowed to raise: the caller is a CLI reporting on documents.
        return [
            _problem(FragmentRef("-", source), f"cannot stage for reading: {exc}"),
            *problems,
        ]


def _bare_name(source: str, index: int | None) -> str:
    stem = source[: -len(suffix)] if (suffix := _suffix_of(source)) else source
    return f"{stem}-{index}.yaml" if index is not None else f"{stem}.yaml"


def _suffix_of(name: str) -> str:
    lowered = name.lower()
    return next((s for s in YAML_SUFFIXES if lowered.endswith(s)), "")


def _from_configmap(
    document: dict, files: dict[str, str], problems: list[LoadedFragment]
) -> None:
    """Extract a labelled ConfigMap's fragment documents, or say why it has none."""
    metadata = document.get("metadata") or {}
    # helm template leaves the namespace to install time, and it is only ever
    # cosmetic here; `-` is what scan() reports for a file without provenance.
    ref = FragmentRef(
        str(metadata.get("namespace") or "-"), str(metadata.get("name") or "(unnamed)")
    )
    labels = metadata.get("labels") or {}
    data = document.get("data") or {}
    if not isinstance(data, dict):
        return

    if str(labels.get(FRAGMENT_LABEL, "")).lower() != FRAGMENT_LABEL_VALUE:
        if any(KIND in str(value) for value in data.values()):
            problems.append(
                _problem(
                    ref,
                    f"holds a fragment but is not labelled "
                    f"{FRAGMENT_LABEL}: {FRAGMENT_LABEL_VALUE!r}, so the "
                    f"reconciler will never discover it",
                )
            )
        return

    written = 0
    refused = 0
    for key, value in data.items():
        suffix = _suffix_of(str(key))
        if not suffix:
            continue
        if "." in str(key)[: -len(suffix)]:
            # The sidecar writes `<namespace>.configmap_<name>.<key>`, and a
            # dotted key makes that unsplittable. Caught here because this is
            # the one place a real ConfigMap is in hand; from the filename
            # alone it is already too late to tell.
            problems.append(
                _problem(
                    ref,
                    f"data key {key!r} would be indistinguishable from part of "
                    f"the ConfigMap name once the discovery sidecar writes it "
                    f"to disk: {DATA_KEY_RULE}",
                )
            )
            refused += 1
            continue
        files[f"namespace_{ref.namespace}.configmap_{ref.name}.{key}"] = (
            value if isinstance(value, str) else yamlio.safe_dump(value)
        )
        written += 1
    if not written and not refused:
        problems.append(
            _problem(
                ref,
                "no data key ends in .yaml, .yml or .json"
                + (f" ({', '.join(sorted(map(str, data)))})" if data else "")
                + ", so the reconciler reads nothing from it",
            )
        )


def _problem(ref: FragmentRef, error: str) -> LoadedFragment:
    return LoadedFragment(ref=ref, fragment=None, content_hash="", errors=[error])


def _assemble(
    ref: FragmentRef, docs: list[tuple[str, str, str | None]]
) -> LoadedFragment:
    docs = sorted(docs)
    content_hash = _hash([(key, text) for key, text, _ in docs])
    errors = [f"{key}: {err}" for key, _, err in docs if err]
    parsed: list[Fragment] = []

    for key, text, read_error in docs:
        if read_error:
            continue
        try:
            loaded = yamlio.safe_load(text)
        except yamlio.YAMLError as exc:
            errors.append(f"{key}: not valid YAML: {_one_line(exc)}")
            continue
        if loaded is None:
            errors.append(f"{key}: empty document")
            continue
        try:
            parsed.append(Fragment.model_validate(loaded))
        except ValidationError as exc:
            errors.extend(f"{key}: {line}" for line in _format_errors(exc))

    fragment = None
    if parsed and not errors:
        try:
            fragment = Fragment(
                apiVersion=parsed[0].apiVersion,
                kind=parsed[0].kind,
                clients=[c for f in parsed for c in f.clients],
            )
        except ValidationError as exc:
            # Every document validated on its own; the ConfigMap as a whole
            # does not (two keys declaring one clientId). Unguarded, this
            # escapes scan() and takes the whole reconcile with it.
            errors.extend(_format_errors(exc))
    elif not parsed and not errors:
        errors.append("no documents found")

    return LoadedFragment(
        ref=ref,
        fragment=fragment,
        content_hash=content_hash,
        errors=errors,
        sources=[key for key, _, _ in docs],
    )


def _format_errors(exc: ValidationError) -> list[str]:
    out = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"]) or "(root)"
        message = err["msg"]
        if err["type"] == "extra_forbidden":
            message = "unknown field (the fragment vocabulary is closed)"
        out.append(f"{loc}: {message}")
    return out


def _one_line(exc: object) -> str:
    return " ".join(str(exc).split())


def _hash(pairs: list[tuple[str, str]]) -> str:
    digest = hashlib.sha256()
    for key, text in pairs:
        digest.update(key.encode("utf-8"))
        digest.update(b"\0")
        digest.update(text.encode("utf-8"))
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()
