"""Mixed activations preserve toggle semantics and per-device preparation."""

from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from test_controller import controller, definitions
from test_interceptor import SplitCallError


class ActivationTests(unittest.IsolatedAsyncioTestCase):
    def fixture(self, *, no_payload=False, fail=None):
        manager, _, _, _, _ = controller()
        scope = manager._service_interceptor_turn_on_handler.__globals__
        scope["deepcopy"] = deepcopy
        scope["CONF_PARAMS"] = "params"
        scope["SplitCallError"] = SplitCallError
        scope["cv"] = SimpleNamespace(ENTITY_SERVICE_FIELDS={
            "entity_id", "device_id", "area_id", "floor_id", "label_id"})
        scope["is_our_context"] = lambda context, which=None: (
            context.id.startswith("our") and (which is None or which in context.id))
        state = {"light.first": "off", "light.second": "off", "light.skipped": "on"}
        history, prepared = [], []
        class Profile:
            initial_transition = 1
            def create_context(self, which, parent=None):
                return SimpleNamespace(id=f"our-{which}", parent_id=parent.id if parent else None)
        switch = Profile()
        manager.create_context = switch.create_context

        def separate(entities, data):
            selected = [eid for eid in entities if state[eid] == "off"]
            skipped = [eid for eid in entities if eid not in selected]
            return ({switch: selected} if selected else {}), skipped
        manager._separate_entity_ids = separate
        manager._correct_for_multi_light_intercept = lambda entities, mapping, skipped: (mapping, skipped)

        async def prepare_first(**kwargs):
            eid = kwargs["entity_ids"][0]
            prepared.append(eid)
            if no_payload and eid == "light.first":
                return
            kwargs["data"]["params"].update(
                color_temp_kelvin=3000 if eid == "light.first" else 5000)
        manager._service_interceptor_turn_on_single_light_handler = prepare_first

        async def dispatch(domain, action, data, *, context, **kwargs):
            entities = data["entity_id"]
            entities = entities if isinstance(entities, list) else [entities]
            # The native schema separates target fields and params before interception.
            native = {"entity_id": list(entities),
                      "params": {key: value for key, value in data.items() if key != "entity_id"}}
            call = SimpleNamespace(service=action, context=context)
            deferred_error = None
            try:
                await manager._service_interceptor_turn_on_handler(call, native)
            except SplitCallError as error:
                deferred_error = error
            for eid in native["entity_id"]:
                history.append((eid, action, dict(native["params"]), context.parent_id))
                state[eid] = ("off" if state[eid] == "on" else "on") if action == "toggle" else "on"
                if eid == fail:
                    # Model a device acting, then its integration reporting a failure.
                    raise RuntimeError("device error after dispatch")
            if deferred_error is not None:
                raise deferred_error
        manager.hass.services = SimpleNamespace(async_call=AsyncMock(side_effect=dispatch))
        return manager, state, history, prepared, dispatch

    async def test_mixed_toggle_changes_each_target_once(self):
        manager, state, history, prepared, dispatch = self.fixture()
        await dispatch("light", "toggle", {
            "entity_id": list(state), "transition": 25,
        }, context=SimpleNamespace(id="user", parent_id=None))
        self.assertEqual(state, {"light.first": "on", "light.second": "on", "light.skipped": "off"})
        self.assertEqual(sorted(eid for eid, *_ in history), sorted(state))
        self.assertEqual(prepared, ["light.first", "light.second"])
        sent = {eid: params for eid, action, params, parent in history}
        self.assertEqual(sent["light.first"]["color_temp_kelvin"], 3000)
        self.assertEqual(sent["light.second"]["color_temp_kelvin"], 5000)
        self.assertNotIn("color_temp_kelvin", sent["light.skipped"])
        self.assertTrue(all(params["transition"] == 25 for params in sent.values()))
        self.assertTrue(all(parent == "user" for eid, action, params, parent in history if eid != "light.first"))

    async def test_unadjustable_first_light_does_not_drop_or_bypass_others(self):
        manager, state, history, prepared, dispatch = self.fixture(no_payload=True)
        await dispatch("light", "turn_on", {"entity_id": ["light.first", "light.second"]},
                       context=SimpleNamespace(id="user", parent_id=None))
        self.assertEqual(state["light.first"], "on")
        self.assertEqual(state["light.second"], "on")
        self.assertEqual(prepared, ["light.first", "light.second"])
        sent = {eid: params for eid, action, params, parent in history}
        self.assertNotIn("color_temp_kelvin", sent["light.first"])
        self.assertEqual(sent["light.second"]["color_temp_kelvin"], 5000)

    async def test_partial_failure_never_replays_an_already_toggled_light(self):
        manager, state, history, prepared, dispatch = self.fixture(fail="light.second")
        with self.assertLogs("test", level="ERROR"), self.assertRaisesRegex(SplitCallError, "light.second"):
            await dispatch("light", "toggle", {"entity_id": list(state)},
                           context=SimpleNamespace(id="user", parent_id=None))
        self.assertEqual(state, {"light.first": "on", "light.second": "on", "light.skipped": "off"})
        self.assertEqual(sorted(eid for eid, *_ in history), sorted(state))

    async def test_prepare_clamps_each_bulb_to_its_own_limits(self):
        scope = {"SwitchEntity": type("SwitchEntity", (), {}),
                 "RestoreEntity": type("RestoreEntity", (), {})}
        manager, flags, _, _, _ = controller()
        class State:
            def __init__(self, maximum):
                self.attributes = {"min_color_temp_kelvin": 2000, "max_color_temp_kelvin": maximum}
        scope.update(
            LightControlAttributes=flags, State=State,
            ATTR_ENTITY_ID="entity_id", ATTR_TRANSITION="transition", ATTR_BRIGHTNESS="brightness",
            ATTR_RGB_COLOR="rgb_color", ATTR_COLOR_TEMP_KELVIN="color_temp_kelvin",
            _supported_features=lambda hass, eid: {"brightness", "color_temp", "transition"},
            clamp=lambda value, low, high: max(low, min(high, value)),
            prepare_adaptation_data=lambda *args, **kwargs: args[5],
            _LOGGER=manager._service_interceptor_turn_on_handler.__globals__["_LOGGER"],
        )
        definitions("switch.py", {"AdaptiveSwitch"}, scope)
        switch = scope["AdaptiveSwitch"].__new__(scope["AdaptiveSwitch"])
        switch.manager = manager
        manager.get_adaption_control_attributes = lambda profile, light: flags.ALL
        switch._name = "Profile"
        switch._prefer_rgb_color = False
        switch._send_split_delay = 0
        switch._separate_turn_on_commands = False
        switch._skip_redundant_commands = True
        switch._schedule = SimpleNamespace(get_settings=lambda transition: {
            "brightness_pct": 40, "color_temp_kelvin": 5000, "rgb_color": (255, 255, 255)})
        states = {"light.first": State(3000), "light.second": State(6500)}
        switch.hass = SimpleNamespace(states=SimpleNamespace(get=states.get))
        a = await switch.prepare_adaptation_data("light.first", 25, context=object())
        b = await switch.prepare_adaptation_data("light.second", 25, context=object())
        self.assertEqual(a["color_temp_kelvin"], 3000)
        self.assertEqual(b["color_temp_kelvin"], 5000)
        # RGB sections must never pass a missing Kelvin value into CCT clamping.
        switch._schedule.get_settings = lambda transition: {
            "brightness_pct": 40, "color_temp_kelvin": None, "rgb_color": (255, 0, 128)}
        cct = await switch.prepare_adaptation_data("light.first", 25, context=object())
        self.assertNotIn("color_temp_kelvin", cct)
        self.assertNotIn("rgb_color", cct)
        self.assertIn("brightness", cct)
        scope["_supported_features"] = lambda hass, eid: {"brightness", "color", "color_temp"}
        rgb = await switch.prepare_adaptation_data("light.second", 25, context=object())
        self.assertEqual(rgb["rgb_color"], (255, 0, 128))
        self.assertNotIn("color_temp_kelvin", rgb)


if __name__ == "__main__":
    unittest.main()
