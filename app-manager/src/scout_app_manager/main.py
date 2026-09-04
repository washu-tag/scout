"""Service entrypoint: wire the layers together and run."""

import logging
import os
import threading

from . import api, metrics, watch
from .health import serve
from .k8s import Client
from .loop import run_forever
from .service import AppManagerService
from .settings import Settings

log = logging.getLogger("app-manager")


def main() -> int:
    level = os.environ.get("APP_MANAGER_LOG_LEVEL", "INFO")
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
    )
    if level.upper() != "DEBUG":
        # A reconcile is a dozen requests and they now run every minute, so at
        # INFO the client's per-request line buries the reconciler's own.
        logging.getLogger("httpx2").setLevel(logging.WARNING)
    settings = Settings()
    client = Client()
    service = AppManagerService(settings, client)
    log.info(
        "starting: namespace=%s domain=%s mode=%s fragments=%s",
        service.namespace,
        settings.domain,
        settings.apply_mode,
        settings.fragment_dir,
    )
    wake = threading.Event()
    threading.Thread(
        target=serve,
        args=(settings.port, service.ready, lambda: metrics.render(service.state)),
        daemon=True,
    ).start()
    threading.Thread(
        target=api.serve, args=(settings.reload_port, wake), daemon=True
    ).start()
    if settings.object_watch:
        # Fragments arrive by the sidecar; these are the two inputs that do
        # not, and neither is selected by label.
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
