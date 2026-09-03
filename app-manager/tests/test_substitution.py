"""What the reconciler knows about config-cli's `$(env:...)` substitution."""

import pytest

from scout_app_manager import substitution


def test_finds_every_reference():
    text = '{"a": "$(env:superset)", "b": "https://x.$(env:server_hostname)/y"}'
    assert substitution.references(text) == {"superset", "server_hostname"}


def test_a_document_with_no_placeholders_needs_nothing():
    assert substitution.references('{"secret": "hunter2"}') == set()


def test_unresolved_names_what_is_missing():
    text = "$(env:superset) $(env:grafana) $(env:minio)"
    assert substitution.unresolved(text, {"grafana"}) == ["minio", "superset"]


@pytest.mark.parametrize(
    "client_id,expected",
    [
        ("hello", "fragment_hello"),
        ("report_viewer_svc", "fragment_report_viewer_svc"),
        ("open-webui", "fragment_open_webui"),
        ("My.App", "fragment_my_app"),
    ],
)
def test_env_names_are_identifiers(client_id, expected):
    assert substitution.env_name(client_id) == expected
    assert substitution.env_name(client_id).isidentifier()


def test_env_names_can_collide():
    """Documented, and why `compose` rejects both parties rather than picking.

    `-` and `.` are legal in a clientId and illegal in an environment variable,
    so the mapping cannot be injective.
    """
    assert substitution.env_name("a-b") == substitution.env_name("a.b")


def test_a_fragment_name_cannot_shadow_a_base_realm_key():
    """The prefix is what keeps the two namespaces apart."""
    base_keys = {"oauth2_proxy", "superset", "launchpad_client", "server_hostname"}
    assert not any(k.startswith(substitution.FRAGMENT_PREFIX) for k in base_keys)
