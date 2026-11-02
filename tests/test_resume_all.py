"""Global reset clears overrides across profiles without enabling paused profiles."""

import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

from test_controller import controller, definitions


class ResumeAllTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_no_loaded_profiles_is_a_safe_noop(self):
        resume, manager, enabled, paused, profile = self.setup_reset()
        manager.hass.data = {}
        await resume(manager.hass)
        enabled._update_attrs_and_maybe_adapt_lights.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
