"""Scheduled lighting with an Adaptive Lighting device controller."""

import asyncio
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry
from homeassistant.helpers.storage import Store

from .const import DOMAIN, ATTR_ADAPTIVE_LIGHTING_MANAGER, CONF_LIGHTS
from .frontend import async_setup_frontend, async_unload_frontend
from .color_and_brightness import DEFAULT_CURVE, validate_curve
from .helpers import expand_lights, light_group_members

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
PLATFORMS = [Platform.SWITCH, Platform.SELECT]


def resolve_profile_lights(hass: HomeAssistant, entry: ConfigEntry) -> list[str]:
    """Wait for referenced entities before freezing the profile's membership."""
    configured = entry.options.get(CONF_LIGHTS, entry.data[CONF_LIGHTS])
    lights = expand_lights(hass, configured)
    registry = entity_registry.async_get(hass)
    for light in sorted(set(configured) | set(lights)):
        state = hass.states.get(light)
        registered = registry.async_get(light)
        if state is None or (
            registered is not None and registered.platform == "group"
            and light_group_members(state) is None
        ):
            raise ConfigEntryNotReady(f"Waiting for light entity {light} to finish setting up")
    for key, profile in hass.data.get(DOMAIN, {}).items():
        if key != entry.entry_id and isinstance(profile, dict):
            overlap = set(lights) & set(profile.get("lights", []))
            if overlap:
                raise ConfigEntryError(f"Lights already controlled by another profile: {', '.join(sorted(overlap))}")
    return lights


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Load persisted per-light modes before the controller can send commands."""
    store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.modes")
    modes = await store.async_load() or {}
    curve_store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.curve")
    curve = validate_curve(await curve_store.async_load() or DEFAULT_CURVE)
    lights = resolve_profile_lights(hass, entry)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "mode_store": store,
        "manual_modes": modes,
        "curve_store": curve_store,
        "curve": curve,
        "curve_lock": asyncio.Lock(),
        "lights": lights,
    }
    entry.async_on_unload(entry.add_update_listener(async_update_options))
    await hass.config_entries.async_forward_entry_setups(entry, [Platform.SWITCH])
    await hass.config_entries.async_forward_entry_setups(entry, [Platform.SELECT])
    await async_setup_frontend(hass)
    return True

async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload entities before detaching the shared controller."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    data = hass.data[DOMAIN]
    switch = data[entry.entry_id]["switch"]
    manager = data[ATTR_ADAPTIVE_LIGHTING_MANAGER]
    # Flush pending mode changes before the profile disappears.
    await data[entry.entry_id]["mode_store"].async_save(data[entry.entry_id]["manual_modes"])
    data.pop(entry.entry_id)
    remaining = {light for value in data.values() if isinstance(value, dict)
                 and (other := value.get("switch")) for light in other.lights}
    for light in set(switch.lights) - remaining:
        manager.reset(light, reset_manual_control=False)
        manager.manual_control.pop(light, None)
        manager.lights.discard(light)
    if not any(isinstance(value, dict) and "switch" in value for value in data.values()):
        manager.disable()
        data.pop(ATTR_ADAPTIVE_LIGHTING_MANAGER)
        async_unload_frontend(hass)
    if not data:
        hass.data.pop(DOMAIN)
    return True
