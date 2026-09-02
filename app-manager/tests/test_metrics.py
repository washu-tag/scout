from conftest import fragment_yaml, setup, write_fragment  # noqa: F401

from scout_app_manager import metrics
from scout_app_manager.models import INVALID, RETRACTING, FragmentStatus, State


def series(text: str) -> dict[str, str]:
    return {
        line.split(" ")[0]: line.split(" ")[1]
        for line in text.splitlines()
        if line and not line.startswith("#")
    }


def test_a_rejected_fragment_is_countable(setup):
    """The series the alert fires on."""
    service, fragments, _ = setup
    write_fragment(fragments, "scout-demo", "hello", fragment_yaml("hello"))
    write_fragment(
        fragments, "evil", "takeover", fragment_yaml("launchpad", roles=[], grants={})
    )

    found = series(metrics.render(service.reconcile_once()))

    assert found['scout_app_manager_fragments{state="rejected"}'] == "1"
    assert found['scout_app_manager_fragments{state="installed"}'] == "1"
    assert found['scout_app_manager_fragments{state="invalid"}'] == "0"
    assert (
        found[
            'scout_app_manager_fragment_state{namespace="evil",name="takeover",'
            'state="rejected"}'
        ]
        == "1"
    )


def test_every_state_is_emitted_even_at_zero(setup):
    """A missing series and a zero series alert differently."""
    service, _, _ = setup

    found = series(metrics.render(service.reconcile_once()))

    for state in ("installed", "invalid", "rejected", "retracting"):
        assert found[f'scout_app_manager_fragments{{state="{state}"}}'] == "0"


def test_the_realm_facts_are_exposed(setup):
    service, fragments, _ = setup
    service.settings.apply_mode = "apply"
    write_fragment(fragments, "scout-demo", "hello", fragment_yaml("hello"))

    found = series(metrics.render(service.reconcile_once()))

    assert found["scout_app_manager_base_realm_applied"] == "1"
    assert found["scout_app_manager_discovery_synced"] == "1"
    assert found["scout_app_manager_apply_pending"] == "0"
    assert float(found["scout_app_manager_last_apply_success_timestamp_seconds"]) > 0
    assert found['scout_app_manager_phase{phase="Applied"}'] == "1"


def test_a_held_retraction_is_visible(setup):
    service, fragments, _ = setup
    service.settings.apply_mode = "apply"
    path = write_fragment(fragments, "scout-demo", "hello", fragment_yaml("hello"))
    service.reconcile_once()
    path.unlink()

    found = series(metrics.render(service.reconcile_once()))

    assert found['scout_app_manager_fragments{state="retracting"}'] == "1"
    assert found['scout_app_manager_phase{phase="Holding"}'] == "1"


def test_label_values_are_escaped():
    """A fragment name cannot break the exposition format."""
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

    text = metrics.render(state)

    assert 'name="we\\"ird name"' in text
    assert len([line for line in text.splitlines() if "fragment_state{" in line]) == 1


def test_an_unset_timestamp_is_zero_not_a_crash():
    assert (
        series(metrics.render(State()))[
            "scout_app_manager_last_apply_success_timestamp_seconds"
        ]
        == "0.0"
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
