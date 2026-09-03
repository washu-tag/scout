"""`scout-app-manager` — the laptop tool and the in-pod inspection path.

`validate`, `compose`, and `diff` run the same code the service runs, with no
cluster: a fragment author should be able to get the service's verdict before
anything is deployed.

`status` and `reconcile` need the cluster, so they are meant to be run inside
the pod (`kubectl exec`). `status` only reads. `reconcile` writes, and is
break-glass only: it is a second process against one realm.
"""

import argparse
import difflib
import logging
import sys
from pathlib import Path

from .compose import FragmentEffect, Site, canonical, compose, plan
from .load import LoadedFragment, parse_realm_document, scan
from .settings import Settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scout-app-manager", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_validate = sub.add_parser(
        "validate", help="check fragments and print what they would do"
    )
    p_validate.add_argument("paths", nargs="+", help="fragment files or a directory")
    p_validate.add_argument("--domain", default="scout.example.edu")

    for name, help_text in (
        ("compose", "merge fragments into a base realm"),
        ("diff", "show what fragments change in a base realm"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--base", required=True, help="base realm JSON")
        p.add_argument("--fragments", required=True, help="fragment directory")
        p.add_argument("--domain", required=True)
        if name == "compose":
            p.add_argument("--out", help="write composed realm here (default stdout)")

    p_reconcile = sub.add_parser(
        "reconcile", help="break-glass: run one reconcile, which may write the realm"
    )
    p_reconcile.add_argument("--once", action="store_true", default=True)

    sub.add_parser("status", help="what is installed, and what is broken (read-only)")

    args = parser.parse_args(argv)
    if args.command == "validate":
        return _validate(args)
    if args.command in ("compose", "diff"):
        return _compose_or_diff(args)
    if args.command == "reconcile":
        return _reconcile()
    return _status()


# --- no cluster needed ----------------------------------------------------


def _collect(paths: list[str]) -> list[LoadedFragment]:
    loaded: list[LoadedFragment] = []
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            loaded.extend(scan(path))
        else:
            loaded.extend(scan_single(path))
    return loaded


def scan_single(path: Path) -> list[LoadedFragment]:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        link = Path(tmp) / path.name
        link.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        return scan(tmp)


def print_effect(effect: FragmentEffect, indent: str = "    ") -> None:
    for client in effect.clients:
        print(f"{indent}client {client.client_id}  [{', '.join(client.login_flows)}]")
        for uri in client.redirect_uris:
            print(f"{indent}    redirect  {uri}")
        for origin in client.web_origins:
            print(f"{indent}    origin    {origin}")
        print(
            f"{indent}    secret    {client.secret_source} "
            f"-> $(env:{client.secret_env})"
        )
        print(
            f"{indent}    pkce      "
            + ("required (S256)" if client.pkce == "required" else "NOT enforced")
        )
        for label, value in sorted(client.lifespans.items()):
            print(f"{indent}    lifespan  {label}: {value}")
        if client.roles:
            print(
                f"{indent}    roles     {', '.join(client.roles)} "
                f"(claim {client.role_claim!r})"
            )
        for group, roles in sorted(client.grants.items()):
            print(f"{indent}    grants    {', '.join(roles)} -> everyone in {group}")


def _validate(args: argparse.Namespace) -> int:
    loaded = _collect(args.paths)
    if not loaded:
        print("no fragments found", file=sys.stderr)
        return 1

    failed = False
    for item in loaded:
        if not item.valid:
            failed = True
            print(f"INVALID  {item.ref}")
            for err in item.errors:
                print(f"    - {err}")
            continue
        effect = plan(item, Site(domain=args.domain))
        if effect.errors:
            failed = True
            print(f"INVALID  {item.ref}")
            for err in effect.errors:
                print(f"    - {err}")
            continue
        print(f"OK       {item.ref}  ({item.content_hash[:19]})")
        print_effect(effect)
    return 1 if failed else 0


def _compose_or_diff(args: argparse.Namespace) -> int:
    base = parse_realm_document(Path(args.base).read_text(encoding="utf-8"))
    realm = base.get("realm_representation", base)

    result = compose(realm, scan(args.fragments), Site(domain=args.domain))

    for item, reasons in result.rejected:
        print(f"rejected {item.ref}: {'; '.join(reasons)}", file=sys.stderr)
    for item in result.accepted:
        print(f"accepted {item.ref}", file=sys.stderr)

    if args.command == "compose":
        text = canonical(result.realm)
        if args.out:
            Path(args.out).write_text(text, encoding="utf-8")
        else:
            sys.stdout.write(text)
        return 0

    diff = difflib.unified_diff(
        canonical(realm).splitlines(keepends=True),
        canonical(result.realm).splitlines(keepends=True),
        fromfile="base realm",
        tofile="composed realm",
    )
    wrote = False
    for line in diff:
        wrote = True
        sys.stdout.write(line)
    if not wrote:
        print("no change: composed realm is identical to the base realm")
    return 0


# --- cluster needed --------------------------------------------------------


def build_service():
    from .k8s import Client
    from .service import AppManagerService

    logging.basicConfig(level="WARNING", format="%(levelname)-7s %(message)s")
    return AppManagerService(Settings(), Client())


def build_store():
    """The read-only half of the cluster wiring, for `status`."""
    from .k8s import Client
    from .status import StatusStore

    logging.basicConfig(level="WARNING", format="%(levelname)-7s %(message)s")
    settings = Settings()
    client = Client()
    namespace = settings.namespace or client.namespace()
    return settings, StatusStore(client, namespace, settings.status_configmap)


def _reconcile() -> int:
    service = build_service()
    state = service.reconcile_once()
    print(f"reconciled at {state.last_reconcile}: {state.last_result}")
    return 0


def _status() -> int:
    """Read the published status; recompute each fragment's effect from disk."""
    settings, store = build_store()
    state = store.load()
    if state is None:
        print(
            f"no status published yet in {store.namespace}/{store.name}",
            file=sys.stderr,
        )
        return 1

    print(f"phase {state.phase} · mode {settings.apply_mode} · {state.last_result}")
    print(
        f"base {(state.base_hash or '-')[:19]} -> "
        f"composed {(state.composed_hash or '-')[:19]}"
        + ("  (identical)" if state.identical_to_base else "")
    )
    print(
        f"applied {(state.last_applied_hash or '-')[:19]} at "
        f"{state.applied_at or 'never'} · last reconcile {state.last_reconcile}"
    )
    print(
        f"discovery synced: {state.discovery_synced} · "
        f"base realm applied: {state.base_realm_applied}"
    )

    on_disk = {str(item.ref): item for item in _collect([settings.fragment_dir])}
    site = Site(domain=settings.domain, signout_url=settings.signout_url)
    for fragment in sorted(state.fragments, key=lambda f: f.ref):
        print()
        print(f"{fragment.status.upper():10} {fragment.ref}  ({fragment.display_name})")
        print(f"           applied  {fragment.content_hash}")
        if fragment.retracting_since:
            print(f"           absent since {fragment.retracting_since}")
        for err in fragment.errors:
            print(f"           ! {err}")
        item = on_disk.pop(fragment.ref, None)
        if item is None:
            print("           (not on disk)")
            continue
        if item.content_hash != fragment.content_hash:
            print(
                f"           on disk  {item.content_hash}  (differs; not yet applied)"
            )
        if item.valid:
            print_effect(plan(item, site), indent="           ")
    for ref in sorted(on_disk):
        print()
        print(f"{'NEW':10} {ref}  (discovered, not in the published status)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
