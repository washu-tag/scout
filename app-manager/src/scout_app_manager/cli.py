"""`scout-app-manager` — the laptop tool and the in-pod inspection path.

`validate` checks a fragment **on its own**, and that boundary is the point:
a fragment author gets the same answer wherever they run it, before there is a
cluster to run it against. It reads a rendered chart, a ConfigMap or a bare
fragment, from files or stdin, so `helm template . | scout-app-manager validate -`
works through `docker run -i` and `kubectl exec -i` alike.

So it is not the reconciler's verdict, and the gap is the rejections that are
about the realm rather than about the fragment: adopting a client the base
realm already declares, a credential variable a site's own Secret already
defines, granting into a group that does not exist, a `secretRef` with nothing
behind it, and a clientId another fragment is claiming. Those need the base
realm, the cluster's Secrets, or the other fragments, and none of the three is
a property of the document in front of you. The reconciler reports them per
fragment, in `status` and in its log.

`status` needs the cluster, so it is meant to be run inside the pod
(`kubectl exec`). Every subcommand here only reads: the running reconciler is
the realm's only writer (ADR 0037), and a second process that could apply one
is exactly what that design forbids.
"""

import argparse
import logging
import sys
from pathlib import Path

from .compose import FragmentEffect, Site, plan
from .load import LoadedFragment, scan, scan_stream
from .schema import FRAGMENT_LABEL, FRAGMENT_LABEL_VALUE
from .settings import Settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scout-app-manager", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_validate = sub.add_parser(
        "validate", help="check fragments and print what they would do"
    )
    p_validate.add_argument(
        "paths", nargs="+", help="fragment files, a directory, or - for stdin"
    )
    p_validate.add_argument("--domain", default="scout.example.edu")
    p_validate.add_argument(
        "--signout-url",
        default="",
        help="the site's platform signout URI (default: derived from --domain)",
    )

    sub.add_parser("status", help="what is installed, and what is broken (read-only)")

    args = parser.parse_args(argv)
    if args.command == "validate":
        return _validate(args)
    return _status()


# --- no cluster needed ----------------------------------------------------


def _collect(paths: list[str]) -> list[LoadedFragment]:
    loaded: list[LoadedFragment] = []
    for raw in paths:
        if raw == "-":
            loaded.extend(scan_stream(sys.stdin.read()))
            continue
        path = Path(raw)
        if path.is_dir():
            loaded.extend(scan(path))
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"cannot read {raw}: {exc.strerror or exc}", file=sys.stderr)
            continue
        loaded.extend(scan_stream(text, path.name))
    return loaded


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
        print(
            "no fragments found: a fragment is a ConfigMap labelled "
            f"{FRAGMENT_LABEL}: {FRAGMENT_LABEL_VALUE!r}",
            file=sys.stderr,
        )
        return 1

    site = Site(domain=args.domain, signout_url=args.signout_url)
    failed = False
    for item in loaded:
        if not item.valid:
            failed = True
            print(f"INVALID  {item.ref}")
            for err in item.errors:
                print(f"    - {err}")
            continue
        effect = plan(item, site)
        if effect.errors:
            failed = True
            print(f"INVALID  {item.ref}")
            for err in effect.errors:
                print(f"    - {err}")
            continue
        print(f"OK       {item.ref}  ({item.content_hash[:19]})")
        print_effect(effect)
    return 1 if failed else 0


# --- cluster needed --------------------------------------------------------


def build_store():
    """The read-only cluster wiring, for `status`."""
    from .k8s import Client
    from .status import StatusStore

    logging.basicConfig(level="WARNING", format="%(levelname)-7s %(message)s")
    settings = Settings()
    client = Client()
    namespace = settings.namespace or client.namespace()
    return settings, StatusStore(client, namespace, settings.status_configmap)


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

    print(f"phase {state.phase} · {state.last_result}")
    print(
        f"base {(state.base_hash or '-')[:19]} -> "
        f"composed {(state.composed_hash or '-')[:19]}"
        + ("  (identical)" if state.identical_to_base else "")
    )
    # What a deploy's `until:` compares against, so it has to be readable here
    # when that wait is the thing that is stuck.
    print(f"base document {(state.base_source_hash or '-')[:19]}")
    print(
        f"applied {(state.last_applied_hash or '-')[:19]} at "
        f"{state.applied_at or 'never'} · last reconcile {state.last_reconcile}"
    )
    print(
        f"discovery synced: {state.discovery_synced} · "
        f"base realm applied: {state.base_realm_applied}"
    )
    if state.realm_unmanaged:
        print(
            f"UNMANAGED: realm {settings.keycloak_realm} is gone, or nothing "
            "has ever imported into it"
        )
    elif state.drift:
        print(
            f"DRIFT: the realm reports import checksum "
            f"{(state.live_checksum or '-')[:12]}, but this reconciler last "
            f"wrote {(state.applied_import_checksum or '-')[:12]}"
        )
    elif not state.live_checksum:
        print("live realm checksum unreadable; drift cannot be detected")

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
