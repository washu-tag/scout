"""Service entrypoint: wire the layers together and run."""

import logging
import os
import threading

from . import health, metrics, reload, shutdown, watch
from .k8s import Client
from .loop import run_forever
from .service import AppManagerService
from .settings import Settings

log = logging.getLogger("app-manager")


def log_level() -> str:
    """The configured level, normalised, or the same SystemExit Settings gives.

    `basicConfig` takes the name verbatim and raises on anything else, before
    any handler exists to report it -- a process dying with a traceback over a
    spelling. Normalising the case here covers the whole of what goes wrong in
    practice, and naming the variable covers the rest.
    """
    level = os.environ.get("APP_MANAGER_LOG_LEVEL", "INFO").strip().upper()
    if level not in logging.getLevelNamesMapping():
        raise SystemExit(f"APP_MANAGER_LOG_LEVEL: not a log level, not {level!r}")
    return level


def main() -> int:
    level = log_level()
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
    )
    if level != "DEBUG":
        # A reconcile is a dozen requests. At INFO the client's per-request
        # line buries the reconciler's own.
        logging.getLogger("httpx2").setLevel(logging.WARNING)
    settings = Settings()
    client = Client()
    service = AppManagerService(settings, client)
    log.info(
        "starting: namespace=%s domain=%s fragments=%s",
        service.namespace,
        settings.domain,
        settings.fragment_dir,
    )
    wake = threading.Event()
    # The loop parks on `wake`, so a stop has to ring it to be noticed.
    shutdown.install(wake)
    listeners = [
        health.serve(
            settings.port, service.ready, lambda: metrics.render(service.state)
        ),
        reload.serve(settings.reload_port, wake),
    ]
    # Fragments arrive by the sidecar; these are the two inputs that do not,
    # and neither is selected by label.
    for resource, names in (
        ("secrets", service.watched_secrets),
        ("configmaps", service.watched_configmaps),
    ):
        threading.Thread(
            target=watch.run_forever,
            args=(client, service.namespace, resource, names, wake),
            daemon=True,
        ).start()
    run_forever(service, settings, wake)
    # Stop answering before the process goes, so kubelet sees the socket close
    # rather than a connection that opens and then dies mid-reply. The watches
    # are left to the interpreter: they are daemon threads parked on a socket
    # read no signal reaches, and nothing depends on how they end.
    for listener in listeners:
        listener.shutdown()
        listener.server_close()
    log.info("stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
