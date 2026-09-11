"""One process-wide stop signal, and the only way anything here waits.

Installing a SIGTERM handler takes away the default disposition, which is to
kill the process outright. So every wait in the reconciler has to end on the
same event or a restart sits out whichever one the signal happened to land in:
the loop's resync floor, a watch's backoff, the poll on an apply Job, the
initial wait for discovery. `sleep` is that wait, and there is no bare
`time.sleep` left on a path a signal can reach.

Nothing is finished on the way out. An apply Job is a pod of its own and goes
on running; the next process finds it by name and waits on it rather than
starting a second writer. Abandoning the wait is therefore free, and holding
the pod open for a five-minute import would only run down the termination
grace and end in SIGKILL anyway.
"""

import logging
import signal
import threading

log = logging.getLogger("app-manager")

requested = threading.Event()


def install(*also_set: threading.Event) -> None:
    """Handle SIGTERM and SIGINT by asking for a stop.

    `also_set` are the events some thread may be parked on -- the reconcile
    loop's doorbell -- which have to be rung for it to reach its own check.
    """
    for number in (signal.SIGTERM, signal.SIGINT):
        signal.signal(number, _request)
    _extra.extend(also_set)


def sleep(seconds: float) -> bool:
    """Wait out `seconds`, or less. False when a stop was asked for meanwhile."""
    return not requested.wait(seconds)


_extra: list[threading.Event] = []


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
