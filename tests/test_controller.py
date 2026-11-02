"""Exercise retained controller methods with small HA boundary doubles.

These are not a full Home Assistant integration test. Methods are compiled from
the production AST so the tests do not need to install HA on Windows.
"""

import ast
import asyncio
from enum import IntFlag, auto
import logging
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

ROOT = Path(__file__).resolve().parents[1] / "custom_components/custom_lighting"


def definitions(filename, names, namespace):
    tree = ast.parse((ROOT / filename).read_text(encoding="utf-8"))
    nodes = [node for node in tree.body if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
             and node.name in names]
    if len(nodes) != len(names):
        raise AssertionError("A tested definition no longer exists")
    future = ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)
    module = ast.fix_missing_locations(ast.Module(body=[future, *nodes], type_ignores=[]))
    exec(compile(module, str(ROOT / filename), "exec"), namespace)


def controller():
    signals = []
    namespace = {
        "IntFlag": IntFlag, "auto": auto, "_LOGGER": logging.getLogger("test"),
        "DOMAIN": "custom_lighting", "MODE_SIGNAL": "mode",
        "async_dispatcher_send": lambda *args: signals.append(args),
        "STATE_ON": "on", "STATE_OFF": "off", "LIGHT_DOMAIN": "light",
        "ATTR_ENTITY_ID": "entity_id", "ATTR_DOMAIN": "domain", "ATTR_SERVICE": "service",
        "ATTR_SERVICE_DATA": "service_data", "ATTR_TRANSITION": "transition",
        "SERVICE_TURN_ON": "turn_on", "SERVICE_TURN_OFF": "turn_off", "SERVICE_TOGGLE": "toggle",
        "ATTR_FLASH": "flash", "ATTR_EFFECT": "effect",
        "BRIGHTNESS_ATTRS": {"brightness", "brightness_pct", "brightness_step", "brightness_step_pct"},
        "COLOR_ATTRS": {"color_temp_kelvin", "rgb_color", "xy_color"},
        "is_our_context": lambda context, which=None: context.id.startswith("our"),
        "cv": SimpleNamespace(ensure_list_csv=lambda value: value if isinstance(value, list) else [value]),
        "target_entities": lambda hass, data: set(
            data.get("entity_id", []) if isinstance(data.get("entity_id", []), list)
            else [data["entity_id"]]),
    }
    definitions("adaptation_utils.py", {"LightControlAttributes", "has_brightness_attribute",
        "has_color_attribute", "has_effect_attribute", "get_light_control_attributes"}, namespace)
    definitions("switch.py", {"AdaptiveLightingManager"}, namespace)
    flags = namespace["LightControlAttributes"]
    manager = namespace["AdaptiveLightingManager"].__new__(namespace["AdaptiveLightingManager"])
    for key in ("manual_control", "our_last_state_on_change", "last_service_data",
                "adaptation_tasks_brightness", "adaptation_tasks_color", "transition_timers",
                "turn_on_event", "turn_off_event", "toggle_event", "sleep_tasks",
                "on_to_off_event", "off_to_on_event", "_proactively_adapting_contexts"):
        setattr(manager, key, {})
    manager.lights = {"light.one", "light.two"}
    switch = SimpleNamespace(lights=["light.one", "light.two"], entity_id="switch.profile",
        async_write_ha_state=Mock(),
        adapt_color_switch=SimpleNamespace(is_on=True), adapt_brightness_switch=SimpleNamespace(is_on=True),
        fire_manual_control_event=Mock())
    store = SimpleNamespace(async_delay_save=Mock())
    profile = {"switch": switch, "manual_modes": {}, "mode_store": store}
    manager.hass = SimpleNamespace(data={"custom_lighting": {"profile": profile}})
    namespace["_switch_with_lights"] = lambda *args, **kwargs: switch
    namespace["NoSwitchFoundError"] = ValueError
    return manager, flags, switch, profile, signals


class ControllerTests(unittest.IsolatedAsyncioTestCase):
    async def test_brightness_override_pauses_both_curves_for_one_light(self):
        manager, flags, switch, profile, signals = controller()
        manager.turn_on_event["light.one"] = SimpleNamespace(
            context=SimpleNamespace(id="external"), data={"service_data": {"brightness": 80}})
        await manager.update_manually_controlled_from_event(switch, "light.one", False)
        self.assertEqual(manager.manual_control["light.one"], flags.ALL)
        self.assertEqual(manager.get_adaption_control_attributes(switch, "light.one"), flags.NONE)
        self.assertEqual(manager.get_adaption_control_attributes(switch, "light.two"), flags.ALL)
        self.assertTrue(profile["manual_modes"]["light.one"])
        self.assertTrue(signals)
        profile["mode_store"].async_delay_save.assert_called()

    async def test_own_commands_do_not_pause(self):
        manager, flags, switch, _, _ = controller()
        manager.turn_on_event["light.one"] = SimpleNamespace(
            context=SimpleNamespace(id="our-command"), data={"service_data": {"brightness": 80}})
        await manager.update_manually_controlled_from_event(switch, "light.one", False)
        self.assertEqual(manager.get_manual_control_attributes("light.one"), flags.NONE)

    async def test_turn_off_service_and_state_change_preserve_manual_mode(self):
        manager, flags, switch, profile, _ = controller()
        manager.set_manual_control_attributes("light.one")
        event = SimpleNamespace(data={"domain": "light", "service": "turn_off",
            "service_data": {"entity_id": ["light.one"]}}, context=SimpleNamespace(id="external"))
        await manager.turn_on_off_event_listener(event)
        self.assertEqual(manager.manual_control["light.one"], flags.ALL)
        event.data = {"entity_id": "light.one",
                      "old_state": SimpleNamespace(state="on"),
                      "new_state": SimpleNamespace(state="off")}
        await manager.state_changed_event_listener(event)
        self.assertEqual(manager.manual_control["light.one"], flags.ALL)
        manager.reset("light.one")
        self.assertFalse(profile["manual_modes"]["light.one"])

    async def test_manual_mode_cancels_a_split_command(self):
        manager, flags, _, _, _ = controller()
        task = asyncio.create_task(asyncio.sleep(30))
        manager.adaptation_tasks_brightness["light.one"] = task
        manager.set_manual_control_attributes("light.one", flags.BRIGHTNESS)
        await asyncio.sleep(0)
        self.assertTrue(task.cancelled())

    async def test_profile_off_on_preserves_manual_mode(self):
        manager, flags, _, _, _ = controller()
        namespace = {"SwitchEntity": type("SwitchEntity", (), {}),
                     "RestoreEntity": type("RestoreEntity", (), {}),
                     "_LOGGER": logging.getLogger("test"), "STATE_ON": "on"}
        definitions("switch.py", {"AdaptiveSwitch"}, namespace)
        switch = namespace["AdaptiveSwitch"].__new__(namespace["AdaptiveSwitch"])
        switch.manager = manager
        switch.lights = ["light.one"]
        switch._name = "Profile"
        switch._state = False
        switch.initial_transition = 1
        switch.create_context = lambda which: which
        switch._setup_listeners = AsyncMock()
        switch._remove_listeners = Mock()
        switch.async_write_ha_state = Mock()
        switch._update_attrs_and_maybe_adapt_lights = AsyncMock()
        manager.set_manual_control_attributes("light.one")
        await switch.async_turn_on()
        await switch.async_turn_off()
        await switch.async_turn_on()
        self.assertEqual(manager.manual_control["light.one"], flags.ALL)

    async def test_pending_update_stops_if_mode_changes_after_preparation(self):
        manager, flags, _, _, _ = controller()
        namespace = {"SwitchEntity": type("SwitchEntity", (), {}),
                     "RestoreEntity": type("RestoreEntity", (), {}),
                     "_LOGGER": logging.getLogger("test")}
        definitions("switch.py", {"AdaptiveSwitch"}, namespace)
        switch = namespace["AdaptiveSwitch"].__new__(namespace["AdaptiveSwitch"])
        switch.manager = manager
        switch._state = True
        switch.hass = SimpleNamespace(services=SimpleNamespace(async_call=AsyncMock()))
        data = SimpleNamespace(max_length=1, initial_sleep=False,
            entity_id="light.one",
            next_service_call_data=AsyncMock(return_value={"brightness": 50}))
        manager.set_manual_control_attributes("light.one")
        await switch._execute_adaptation_calls(data)
        switch.hass.services.async_call.assert_not_awaited()

    async def test_explicit_resume_targets_only_requested_light(self):
        namespace = {}
        definitions("select.py", {"async_set_mode"}, namespace)
        namespace["MODES"] = ["Schedule", "Manual"]
        manager, flags, switch, _, _ = controller()
        switch.manager = manager
        switch.is_on = True
        switch.initial_transition = 1
        switch.create_context = lambda which: which
        switch._update_attrs_and_maybe_adapt_lights = AsyncMock()
        manager.set_manual_control_attributes("light.one")
        manager.set_manual_control_attributes("light.two")
        await namespace["async_set_mode"](manager.hass, switch, "light.one", "Schedule")
        self.assertEqual(manager.manual_control["light.one"], flags.NONE)
        self.assertEqual(manager.manual_control["light.two"], flags.ALL)
        self.assertEqual(switch._update_attrs_and_maybe_adapt_lights.call_args.kwargs["lights"], ["light.one"])
        switch.is_on = False
        switch._update_attrs_and_maybe_adapt_lights.reset_mock()
        await namespace["async_set_mode"](manager.hass, switch, "light.one", "Schedule")
        switch._update_attrs_and_maybe_adapt_lights.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
