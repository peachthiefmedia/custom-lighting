"""A persistent Manual/Schedule mode for each controlled light."""

import asyncio
from homeassistant.components.select import SelectEntity
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import DOMAIN, MODE_SIGNAL

MODES = ["Schedule", "Manual"]


async def async_resume_all(hass):
    """Clear every manual override, then apply schedules without powering lights on."""
    switches = [profile["switch"] for profile in hass.data.get(DOMAIN, {}).values()
                if isinstance(profile, dict) and "switch" in profile
                and not getattr(profile["switch"], "_unloading", False)]
    for switch in switches:
        switch.manager.reset(*switch.lights)
    results = await asyncio.gather(*(
        switch._update_attrs_and_maybe_adapt_lights(
            context=switch.create_context("resume_all"),
            lights=switch.lights, transition=switch.initial_transition,
        ) for switch in switches if switch.is_on
    ), return_exceptions=True)
    failures = [str(result) for result in results if isinstance(result, Exception)]
    if failures:
        raise HomeAssistantError("Modes reset, but applying schedules failed: " + "; ".join(failures))


async def async_set_mode(hass, switch, light, mode):
    """Change mode without changing the power state of a light."""
    if mode not in MODES or light not in switch.lights:
        raise ValueError("Choose a configured light and Schedule or Manual.")
    manager = switch.manager
    if mode == "Manual":
        manager.set_manual_control_attributes(light)
    else:
        manager.reset(light)
        if switch.is_on:
            await switch._update_attrs_and_maybe_adapt_lights(
                context=switch.create_context("resume"),
                lights=[light], transition=switch.initial_transition,
            )


async def async_setup_entry(hass, entry, async_add_entities):
    profile = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([LightScheduleMode(entry, profile["switch"], light)
                        for light in profile["switch"].lights])


class LightScheduleMode(SelectEntity):
    """The controller is the single source of truth for manual mode."""

    _attr_options = MODES
    _attr_icon = "mdi:calendar-clock"
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry, switch, light):
        self._switch = switch
        self._light = light
        self._attr_unique_id = f"{entry.entry_id}_{light}_mode"
        state = switch.hass.states.get(light)
        name = state.name if state else light
        self._attr_name = f"{name} mode"
        self._attr_device_info = switch.device_info

    @property
    def current_option(self):
        return "Manual" if self._switch.manager.manual_control.get(self._light) else "Schedule"

    @property
    def extra_state_attributes(self):
        return {"light": self._light}

    async def async_added_to_hass(self):
        def changed(light):
            if light == self._light:
                self.async_write_ha_state()
        self.async_on_remove(async_dispatcher_connect(self.hass, MODE_SIGNAL, changed))

    async def async_select_option(self, option):
        await async_set_mode(self.hass, self._switch, self._light, option)
