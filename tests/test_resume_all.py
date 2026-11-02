"""Global reset clears overrides across profiles without enabling paused profiles."""

import asyncio
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

from test_controller import controller, definitions


class ResumeAllTests(unittest.IsolatedAsyncioTestCase):
    async def test_mode_notification_updates_select_on_event_loop(self):
        def callback(function):
            function._hass_callback = True
            return function

        connect = Mock(return_value=lambda: None)
        scope = {"SelectEntity": object, "MODES": ["Schedule", "Manual"],
                 "callback": callback, "async_dispatcher_connect": connect, "MODE_SIGNAL": "mode"}
        definitions("select.py", {"LightScheduleMode"}, scope)
        entity = scope["LightScheduleMode"].__new__(scope["LightScheduleMode"])
        entity.hass = object()
        entity._light = "light.one"
        entity.async_on_remove = Mock()
        loop_thread = threading.get_ident()
        def write_state():
            self.assertEqual(threading.get_ident(), loop_thread)
        entity.async_write_ha_state = Mock(side_effect=write_state)
        await entity.async_added_to_hass()
        listener = connect.call_args.args[2]
        # HA dispatches ordinary sync listeners through its executor.
        if getattr(listener, "_hass_callback", False):
            listener("light.one")
        else:
            await asyncio.to_thread(listener, "light.one")
        entity.async_write_ha_state.assert_called_once()
        listener("light.other")
        entity.async_write_ha_state.assert_called_once()

    async def test_global_action_is_registered_and_calls_reset(self):
        resume = AsyncMock()
        registered = Mock()
        scope = {"DOMAIN": "custom_lighting", "async_resume_all": resume,
                 "vol": SimpleNamespace(Schema=lambda schema: schema)}
        definitions("__init__.py", {"async_setup"}, scope)
        hass = SimpleNamespace(services=SimpleNamespace(async_register=registered))
        self.assertTrue(await scope["async_setup"](hass, {}))
        args = registered.call_args.args
        self.assertEqual(args[:2], ("custom_lighting", "resume_schedule"))
        await args[2](SimpleNamespace(data={}))
        resume.assert_awaited_once_with(hass)

    def setup_reset(self):
        manager, flags, enabled, profile, signals = controller()
        enabled.manager = manager
        enabled.is_on = True
        enabled.initial_transition = 1
        enabled._transition = 25
        enabled.create_context = lambda which: which
        enabled._update_attrs_and_maybe_adapt_lights = AsyncMock()
        paused = SimpleNamespace(manager=manager, lights=["light.three"], is_on=False,
            entity_id="switch.paused", async_write_ha_state=Mock())
        paused._update_attrs_and_maybe_adapt_lights = AsyncMock()
        manager.hass.data["custom_lighting"]["paused"] = {
            "switch": paused, "manual_modes": {},
            "mode_store": SimpleNamespace(async_delay_save=Mock()),
        }
        for light in ["light.one", "light.two", "light.three"]:
            manager.set_manual_control_attributes(light)
        scope = {"asyncio": asyncio, "DOMAIN": "custom_lighting", "HomeAssistantError": RuntimeError}
        definitions("select.py", {"async_resume_all"}, scope)
        return scope["async_resume_all"], manager, enabled, paused, profile

    async def test_all_modes_reset_and_only_enabled_profiles_apply(self):
        resume, manager, enabled, paused, profile = self.setup_reset()
        await resume(manager.hass)
        self.assertFalse(any(manager.manual_control.values()))
        self.assertFalse(profile["manual_modes"]["light.one"])
        enabled._update_attrs_and_maybe_adapt_lights.assert_awaited_once()
        self.assertEqual(enabled._update_attrs_and_maybe_adapt_lights.call_args.kwargs["transition"], 25)
        self.assertEqual(enabled._update_attrs_and_maybe_adapt_lights.call_args.kwargs["lights"],
                         ["light.one", "light.two"])
        self.assertNotIn("force", enabled._update_attrs_and_maybe_adapt_lights.call_args.kwargs)
        paused._update_attrs_and_maybe_adapt_lights.assert_not_awaited()
        self.assertFalse(paused.is_on)

    async def test_failure_still_resets_every_light_and_is_reported(self):
        resume, manager, enabled, paused, profile = self.setup_reset()
        enabled._update_attrs_and_maybe_adapt_lights.side_effect = ValueError("device error")
        with self.assertRaisesRegex(RuntimeError, "device error"):
            await resume(manager.hass)
        self.assertFalse(any(manager.manual_control.values()))

    async def test_scheduled_lights_keep_their_existing_fades(self):
        resume, manager, enabled, paused, profile = self.setup_reset()
        manager.reset("light.two")
        original_reset = manager.reset
        manager.reset = Mock(wraps=original_reset)
        await resume(manager.hass)
        self.assertEqual(enabled._update_attrs_and_maybe_adapt_lights.call_args.kwargs["lights"],
                         ["light.one"])
        self.assertNotIn(unittest.mock.call("light.two"), manager.reset.call_args_list)
        enabled._update_attrs_and_maybe_adapt_lights.reset_mock()
        manager.reset.reset_mock()
        await resume(manager.hass)
        manager.reset.assert_not_called()
        enabled._update_attrs_and_maybe_adapt_lights.assert_not_awaited()

    async def test_no_loaded_profiles_is_a_safe_noop(self):
        resume, manager, enabled, paused, profile = self.setup_reset()
        manager.hass.data = {}
        await resume(manager.hass)
        enabled._update_attrs_and_maybe_adapt_lights.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
