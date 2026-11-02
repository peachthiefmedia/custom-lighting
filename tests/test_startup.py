"""Startup ordering and one-time frontend registration regression tests."""

import asyncio
import base64
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

from test_controller import definitions


class NotReady(Exception):
    pass


class EntryError(Exception):
    pass


def resolver(states, platforms=None, profiles=None):
    registry = SimpleNamespace(async_get=lambda eid: (
        SimpleNamespace(platform=platforms[eid]) if platforms and eid in platforms else None))
    scope = {
        "CONF_LIGHTS": "lights", "DOMAIN": "custom_lighting",
        "ConfigEntryNotReady": NotReady, "ConfigEntryError": EntryError,
        "entity_registry": SimpleNamespace(async_get=lambda hass: registry),
    }
    definitions("helpers.py", {"light_group_members", "expand_lights"}, scope)
    definitions("__init__.py", {"resolve_profile_lights"}, scope)
    hass = SimpleNamespace(states=SimpleNamespace(get=states.get),
                           data={"custom_lighting": profiles or {}})
    entry = SimpleNamespace(entry_id="profile", data={"lights": ["light.group"]}, options={})
    return scope["resolve_profile_lights"], hass, entry


class StartupTests(unittest.TestCase):
    def test_profiles_with_shared_name_prefix_have_distinct_contexts(self):
        scope = {
            "base64": base64, "sha256": sha256, "_DOMAIN_SHORT": "cl",
            "ulid_transform": SimpleNamespace(ulid_now=lambda: "0123456789ABCDEFGHIJKLMNOP"),
            "Context": SimpleNamespace,
        }
        definitions("helpers.py", {"short_hash", "remove_vowels", "int_to_base36"}, scope)
        definitions("switch.py", {"create_context"}, scope)
        create = scope["create_context"]
        parent = SimpleNamespace(id="parent")
        a = create("Living room", "turn_on", 0, parent)
        b = create("Living room lamps", "turn_on", 0, parent)
        c = create("Living room", "turn_on", 1, parent)
        self.assertEqual(len(a.id), 26)
        self.assertEqual(len({a.id, b.id, c.id}), 3)
        self.assertEqual(a.parent_id, "parent")

    def test_group_missing_at_boot_retries_instead_of_freezing_as_leaf(self):
        states = {}
        resolve, hass, entry = resolver(states, {"light.group": "group"})
        with self.assertRaises(NotReady):
            resolve(hass, entry)
        states.update({
            "light.group": SimpleNamespace(attributes={"entity_id": ["light.member"]}),
            "light.member": SimpleNamespace(attributes={}),
        })
        self.assertEqual(resolve(hass, entry), ["light.member"])

    def test_group_state_without_members_retries_until_ready(self):
        states = {"light.group": SimpleNamespace(attributes={})}
        resolve, hass, entry = resolver(states, {"light.group": "group"})
        with self.assertRaises(NotReady):
            resolve(hass, entry)

    def test_nested_missing_group_and_member_retry(self):
        states = {"light.group": SimpleNamespace(attributes={"entity_id": ["light.nested"]})}
        resolve, hass, entry = resolver(states, {"light.group": "group", "light.nested": "group"})
        with self.assertRaises(NotReady):
            resolve(hass, entry)
        states["light.nested"] = SimpleNamespace(attributes={"entity_id": ["light.member"]})
        with self.assertRaises(NotReady):
            resolve(hass, entry)
        states["light.member"] = SimpleNamespace(attributes={})
        self.assertEqual(resolve(hass, entry), ["light.member"])

    def test_wrapper_source_is_not_mistaken_for_group_members(self):
        states = {"light.group": SimpleNamespace(attributes={"entity_id": "switch.source"})}
        resolve, hass, entry = resolver(states, {"light.group": "switch_as_x"})
        self.assertEqual(resolve(hass, entry), ["light.group"])

    def test_unavailable_entities_with_states_do_not_block_setup(self):
        states = {
            "light.group": SimpleNamespace(attributes={"entity_id": ["light.member"]}),
            "light.member": SimpleNamespace(state="unavailable", attributes={}),
        }
        resolve, hass, entry = resolver(states, {"light.group": "group"})
        self.assertEqual(resolve(hass, entry), ["light.member"])

    def test_group_overlap_with_another_starting_profile_is_rejected(self):
        states = {
            "light.group": SimpleNamespace(attributes={"entity_id": ["light.member"]}),
            "light.member": SimpleNamespace(attributes={}),
        }
        resolve, hass, entry = resolver(states, profiles={"other": {"lights": ["light.member"]}})
        with self.assertRaises(EntryError):
            resolve(hass, entry)


class FrontendStartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_simultaneous_profiles_register_frontend_once(self):
        hass = SimpleNamespace(data={}, http=SimpleNamespace())
        async def register_paths(paths):
            await asyncio.sleep(0.01)
        async def register_panel(hass, **kwargs):
            await asyncio.sleep(0.01)
            hass.data.setdefault("frontend_panels", {})["lighting"] = object()
        hass.http.async_register_static_paths = AsyncMock(side_effect=register_paths)
        register_panel_mock = AsyncMock(side_effect=register_panel)
        register_command = Mock()
        scope = {
            "asyncio": asyncio, "FRONTEND_LOCK": "lock", "REGISTERED": "registered",
            "PANEL": "lighting", "STATIC_URL": "/lighting", "Path": Path, "__file__": __file__,
            "StaticPathConfig": lambda *args: args,
            "ws_profiles": object(), "ws_save_curve": object(), "ws_set_mode": object(),
            "websocket_api": SimpleNamespace(async_register_command=register_command),
            "panel_custom": SimpleNamespace(async_register_panel=register_panel_mock),
        }
        definitions("frontend.py", {"async_setup_frontend"}, scope)
        setup = scope["async_setup_frontend"]
        await asyncio.gather(setup(hass), setup(hass), setup(hass))
        hass.http.async_register_static_paths.assert_awaited_once()
        register_panel_mock.assert_awaited_once()
        self.assertEqual(register_command.call_count, 3)


if __name__ == "__main__":
    unittest.main()
