"""What a scrape says about the last reconcile.

Read back through Prometheus' own parser rather than by matching lines, so
these assert what a scrape *means* and not how the exposition happens to order
its labels.
"""

from conftest import fragment_yaml, setup, write_fragment  # noqa: F401
from prometheus_client.parser import text_string_to_metric_families

from scout_app_manager import metrics
from scout_app_manager.models import INVALID, RETRACTING, FragmentStatus, State


def scrape(text: str) -> dict[tuple[str, frozenset], float]:
    return {
        (sample.name, frozenset(sample.labels.items())): sample.value
        for family in text_string_to_metric_families(text)
        for sample in family.samples
    }


def value(found: dict, metric: str, /, **labels) -> float:
    return found[(f"{metrics.PREFIX}_{metric}", frozenset(labels.items()))]


def test_a_rejected_fragment_is_countable(setup):
    """The series the alert fires on."""
    service, fragments, _ = setup
    write_fragment(fragments, "scout-demo", "hello", fragment_yaml("hello"))
    write_fragment(
        fragments, "evil", "takeover", fragment_yaml("launchpad", roles=[], grants={})
    )

    found = scrape(metrics.render(service.reconcile_once()))

    assert value(found, "fragments", state="rejected") == 1
    assert value(found, "fragments", state="installed") == 1
    assert value(found, "fragments", state="invalid") == 0
    assert (
        value(
            found,
            "fragment_state",
            namespace="evil",
            name="takeover",
            state="rejected",
        )
        == 1
    )


def test_every_state_is_emitted_even_at_zero(setup):
    """A missing series and a zero series alert differently."""
    service, _, _ = setup

    found = scrape(metrics.render(service.reconcile_once()))

    for state in ("installed", "invalid", "rejected", "retracting"):
        assert value(found, "fragments", state=state) == 0


def test_the_realm_facts_are_exposed(setup):
    service, fragments, _ = setup
    write_fragment(fragments, "scout-demo", "hello", fragment_yaml("hello"))

    found = scrape(metrics.render(service.reconcile_once()))

    assert value(found, "base_realm_applied") == 1
    assert value(found, "discovery_synced") == 1
    assert value(found, "apply_pending") == 0
    assert value(found, "realm_drift") == 0
    assert value(found, "realm_checksum_readable") == 1
    assert value(found, "last_apply_success_timestamp_seconds") > 0
    assert value(found, "phase", phase="Applied") == 1


def test_an_unreachable_keycloak_says_so_rather_than_reading_as_no_drift(setup):
    """0 here means drift detection is off, not that the realm is in step."""
    service, _, _ = setup
    service.reconcile_once()
    service.keycloak.readable = False

    found = scrape(metrics.render(service.reconcile_once()))

    assert value(found, "realm_checksum_readable") == 0
    assert value(found, "realm_drift") == 0


def test_a_retracting_fragment_is_visible(setup):
    """It is composed from cache, so the realm is Applied and it is not."""
    service, fragments, _ = setup
    path = write_fragment(fragments, "scout-demo", "hello", fragment_yaml("hello"))
    service.reconcile_once()
    path.unlink()

    found = scrape(metrics.render(service.reconcile_once()))

    assert value(found, "fragments", state="retracting") == 1
    assert value(found, "phase", phase="Applied") == 1


def test_a_label_value_cannot_break_the_exposition():
    """A fragment names itself, so its name reaches a label unreviewed."""
    state = State(
        fragments=[
            FragmentStatus(
                ref='ns/we"ird',
                namespace="ns",
                name='we"ird\nname',
                status=INVALID,
                content_hash="sha256:x",
            )
        ]
    )

    found = scrape(metrics.render(state))

    assert (
        value(
            found, "fragment_state", namespace="ns", name='we"ird\nname', state=INVALID
        )
        == 1
    )


def test_an_unset_timestamp_is_zero_not_a_crash():
    assert (
        value(scrape(metrics.render(State())), "last_apply_success_timestamp_seconds")
        == 0
    )


def test_a_corrupt_timestamp_is_zero_not_a_crash():
    assert metrics.epoch("nonsense") == 0.0
    assert metrics.epoch(None) == 0.0


def test_the_timestamp_does_not_depend_on_the_process_timezone(monkeypatch):
    """`timegm`, not `mktime`: the stamps are UTC."""
    winter = metrics.epoch("2026-01-15T12:00:00Z")
    summer = metrics.epoch("2026-07-15T12:00:00Z")

    assert winter == 1768478400.0
    assert summer == 1784116800.0
    # No hour lost to a DST boundary.
    assert summer - winter == 181 * 86400


def test_retracting_is_one_of_the_reported_states():
    assert RETRACTING in metrics.STATES
