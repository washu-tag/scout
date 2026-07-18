"""Unit tests for scout_iframe_defaults_event.

The function lazily imports open_webui.models.users, so we inject a fake Users
into sys.modules. It's a pure spy (no merging), so assertions target the payload
the function built rather than the fake.

Run with:
    cd helm/open-webui-bootstrap
    PYTHONPATH=files/payloads uvx --with pytest-asyncio --with pydantic \
        pytest tests/test_scout_iframe_defaults_event.py -v
"""

import sys
import types

import pytest


class _FakeSettings:
    def __init__(self, data):
        self._data = data

    def model_dump(self):
        return dict(self._data)


class _FakeUser:
    def __init__(self, user_id, settings):
        self.id = user_id
        self.settings = _FakeSettings(settings) if settings is not None else None


class _FakeUsersTable:
    def __init__(self):
        self.store = {}
        self.updates = []

    async def get_user_by_id(self, user_id, db=None):
        return (
            _FakeUser(user_id, self.store[user_id]) if user_id in self.store else None
        )

    async def update_user_settings_by_id(self, user_id, updated, db=None):
        self.updates.append((user_id, updated))

    async def get_users(self, *args, **kwargs):
        return {"users": [_FakeUser(uid, s) for uid, s in self.store.items()]}


@pytest.fixture
def fake_users(monkeypatch):
    table = _FakeUsersTable()
    mod = types.ModuleType("open_webui.models.users")
    mod.Users = table
    monkeypatch.setitem(sys.modules, "open_webui", types.ModuleType("open_webui"))
    monkeypatch.setitem(
        sys.modules, "open_webui.models", types.ModuleType("open_webui.models")
    )
    monkeypatch.setitem(sys.modules, "open_webui.models.users", mod)
    return table


@pytest.fixture
def event_instance():
    from scout_iframe_defaults_event import Event

    return Event()


def _written_ui(updates, user_id):
    for uid, updated in updates:
        if uid == user_id:
            return updated.get("ui")
    return None


BOTH_ON = {"iframeSandboxAllowSameOrigin": True, "iframeSandboxAllowForms": True}


@pytest.mark.asyncio
async def test_user_created_writes_both_flags(fake_users, event_instance):
    fake_users.store["u1"] = None
    await event_instance.event({"actor": {"id": "u1"}}, __event_name__="user.created")
    assert _written_ui(fake_users.updates, "u1") == BOTH_ON


@pytest.mark.asyncio
async def test_own_function_enabled_backfills_all_and_preserves_prefs(
    fake_users, event_instance
):
    fake_users.store = {
        "unset": None,
        "partial": {"ui": {"theme": "light"}},
        "done": {"ui": dict(BOTH_ON)},
    }
    await event_instance.event(
        {"subject": {"id": "me"}}, __event_name__="function.enabled", __id__="me"
    )
    assert _written_ui(fake_users.updates, "unset") == BOTH_ON
    assert _written_ui(fake_users.updates, "partial") == {"theme": "light", **BOTH_ON}
    assert _written_ui(fake_users.updates, "done") is None  # already correct → no write


@pytest.mark.asyncio
async def test_other_function_update_does_not_backfill(fake_users, event_instance):
    fake_users.store["u2"] = None
    # a sibling function's update must not trigger our sweep
    await event_instance.event(
        {"subject": {"id": "link_sanitizer_filter"}},
        __event_name__="function.updated",
        __id__="me",
    )
    assert fake_users.updates == []


@pytest.mark.asyncio
async def test_ignores_unrelated_events(fake_users, event_instance):
    fake_users.store["u2"] = None
    await event_instance.event({"actor": {"id": "u2"}}, __event_name__="chat.created")
    assert fake_users.updates == []
