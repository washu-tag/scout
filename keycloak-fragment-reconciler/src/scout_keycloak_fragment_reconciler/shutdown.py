"""One process-wide stop signal, and the only way anything here waits.

Handling SIGTERM takes away the default disposition of dying immediately, so
every wait has to end on the same event or a restart sits out whichever one the
signal landed in. `sleep` is that wait; no bare `time.sleep` is left on a path a
signal can reach.

Nothing is finished on the way out, and nothing needs to be: apply order makes
every prefix inert, so being cut off mid-apply leaves a safe partial state and
the next pass completes it.
"""

import logging
import signal
import threading

log = logging.getLogger("keycloak-fragment-reconciler")

requested = threading.Event()

_extra: list[threading.Event] = []


def install(*also_set: threading.Event) -> None:
    """Handle SIGTERM and SIGINT by asking for a stop.

    `also_set` are events a thread may be parked on, which have to be rung for
    it to reach its own check.
    """
    for number in (signal.SIGTERM, signal.SIGINT):
        signal.signal(number, _request)
    _extra.extend(also_set)


def sleep(seconds: float) -> bool:
    """Wait out `seconds`, or less. False when a stop was asked for meanwhile."""
    return not requested.wait(seconds)


def _request(number: int, _frame) -> None:
    if requested.is_set():
        # A second signal is someone who has waited long enough. Give up the
        # orderly exit and let the default disposition end the process.
        signal.signal(number, signal.SIG_DFL)
        signal.raise_signal(number)
        return
    log.info("%s received; shutting down", signal.Signals(number).name)
    requested.set()
    for event in _extra:
        event.set()
