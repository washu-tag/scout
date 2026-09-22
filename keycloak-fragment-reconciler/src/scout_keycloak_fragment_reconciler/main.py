"""Parse the environment, wire the pieces, start the threads. No logic."""

import logging
import os
import sys
import threading

from . import core, health, metrics, shutdown, watch
from .k8s import Client
from .keycloak import Admin
from .settings import ENV_PREFIX, RequiredSettings

log = logging.getLogger("keycloak-fragment-reconciler")


def resolve_log_level(value: str) -> tuple[int, str | None]:
    """The level to configure, and the value to complain about if there is one.

    Names the logging module carries are honoured, aliases and lowercase
    included, as is a number that is one of their values. Anything else is
    INFO, handed back so the caller can name it once logging is up: this is the
    one setting read before `Settings` can validate anything, and a typo in it
    must not be the reason the process never starts.
    """
    levels = logging.getLevelNamesMapping()
    name = value.strip().upper()
    if name in levels:
        return levels[name], None
    if name.isdecimal() and int(name) in levels.values():
        return int(name), None
    return logging.INFO, value


def main() -> int:
    requested = os.environ.get(f"{ENV_PREFIX}LOG_LEVEL", "INFO")
    level, unknown = resolve_log_level(requested)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if unknown is not None:
        log.warning(
            "%sLOG_LEVEL=%r is not a log level, using INFO. Set one of %s.",
            ENV_PREFIX,
            unknown,
            ", ".join(sorted(logging.getLevelNamesMapping())),
        )
    # httpx2 logs every request at INFO, which buries the handful of lines that
    # say what actually changed. Raise the log level to see them.
    logging.getLogger("httpx2").setLevel(logging.WARNING)
    logging.getLogger("httpcore2").setLevel(logging.WARNING)
    settings = RequiredSettings()
    if settings.dry_run:
        log.warning("dry-run: every write will be logged and none performed")

    k8s = Client()
    admin = Admin(
        settings.keycloak_url,
        settings.realm,
        settings.client_id,
        settings.client_secret,
    )
    reconciler = core.Reconciler(settings, k8s, admin)

    # Installed before the watch starts, so a SIGTERM during the first pass
    # still ends every wait.
    wake = threading.Event()
    shutdown.install(wake)

    server = health.serve(
        settings.port,
        ready=lambda: reconciler.tiers_present,
        metrics=lambda: metrics.render(reconciler),
    )

    # No watched namespaces means no cluster read grant either, so the watch
    # would retry a 403 forever. Running on is deliberate: the process still
    # serves health and metrics, and the realm is left exactly as it is.
    if settings.watched_namespaces:
        threading.Thread(
            target=watch.run_forever,
            args=(k8s, settings.label_selector, wake, reconciler.witness_deletion),
            name="fragment-watch",
            daemon=True,
        ).start()
    else:
        log.warning(
            "%sWATCHED_NAMESPACES is empty: no fragments will be read and no "
            "clients created or deleted. Set it to ALL, or to a "
            "comma-separated list of namespaces.",
            ENV_PREFIX,
        )

    if settings.watches_all:
        scope = "every namespace"
    else:
        scope = ", ".join(settings.watched_namespaces) or "no namespaces"

    log.info(
        "reconciling fragments labelled %s in %s into realm %s at %s "
        "(resync %ss, orphan grace %ss)",
        settings.label_selector,
        scope,
        settings.realm,
        settings.keycloak_url,
        settings.resync_seconds,
        settings.orphan_grace_seconds,
    )
    try:
        core.run_forever(reconciler, wake)
    finally:
        server.shutdown()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
