"""Serve the curve editor and its authenticated Home Assistant WebSocket API."""

import asyncio
from copy import deepcopy
import hashlib
import json
import logging
from pathlib import Path

import probatio as vol
from homeassistant.components import frontend, panel_custom, websocket_api
from homeassistant.components.http import StaticPathConfig

from .color_and_brightness import DailyLightSettings, validate_curve
from .const import DOMAIN
from .select import async_set_mode

_LOGGER = logging.getLogger(__name__)
STATIC_URL = "/custom_lighting_static"
PANEL = "custom-lighting"
REGISTERED = f"{DOMAIN}_frontend_registered"
FRONTEND_LOCK = f"{DOMAIN}_frontend_lock"


def revision(curve):
    return hashlib.sha256(json.dumps(curve, sort_keys=True).encode()).hexdigest()[:16]


def profile_data(hass, entry_id):
    data = hass.data.get(DOMAIN, {}).get(entry_id)
    if not isinstance(data, dict) or "switch" not in data:
        raise ValueError("Lighting profile is not loaded.")
    return data


def serialize(hass, entry_id, profile):
    switch = profile["switch"]
    targets = switch._schedule.get_settings()
    lights = []
    for light in switch.lights:
        state = hass.states.get(light)
        lights.append({
            "entity_id": light,
            "name": state.name if state else light,
            "state": state.state if state else "unavailable",
            "mode": "Manual" if switch.manager.manual_control.get(light) else "Schedule",
        })
    return {
        "id": entry_id, "name": switch._name, "enabled": bool(switch.is_on),
        "curve": profile["curve"], "revision": revision(profile["curve"]),
        "lights": lights, "targets": targets,
        "timezone": hass.config.time_zone,
    }


@websocket_api.websocket_command({vol.Required("type"): "custom_lighting/profiles"})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_profiles(hass, connection, msg):
    profiles = [serialize(hass, key, value)
                for key, value in hass.data.get(DOMAIN, {}).items()
                if isinstance(value, dict) and "switch" in value]
    connection.send_result(msg["id"], profiles)


@websocket_api.websocket_command({
    vol.Required("type"): "custom_lighting/save_curve",
    vol.Required("entry_id"): str,
    vol.Required("revision"): str,
    vol.Required("curve"): dict,
})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_save_curve(hass, connection, msg):
    try:
        profile = profile_data(hass, msg["entry_id"])
        curve = validate_curve(msg["curve"])
        # Serialize saves so two editors cannot overwrite each other while storage awaits.
        async with profile["curve_lock"]:
            if msg["revision"] != revision(profile["curve"]):
                connection.send_error(msg["id"], "conflict", "The curve changed elsewhere. Revert to load it.")
                return
            await profile["curve_store"].async_save(curve)
            profile["curve"] = deepcopy(curve)
            switch = profile["switch"]
            switch._schedule = DailyLightSettings(curve, switch._schedule.timezone)
            if switch.is_on:
                try:
                    for light in switch.lights:
                        if not switch.manager.manual_control.get(light):
                            switch.manager.reset(light, reset_manual_control=False)
                    await switch._update_attrs_and_maybe_adapt_lights(
                        context=switch.create_context("curve"),
                        transition=switch.initial_transition,
                    )
                except Exception:
                    _LOGGER.exception("Curve saved, but applying it to a light failed")
            connection.send_result(msg["id"], serialize(hass, msg["entry_id"], profile))
    except ValueError as error:
        connection.send_error(msg["id"], "invalid_curve", str(error))


@websocket_api.websocket_command({
    vol.Required("type"): "custom_lighting/set_mode",
    vol.Required("entry_id"): str,
    vol.Required("light"): str,
    vol.Required("mode"): vol.In(["Schedule", "Manual"]),
})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_set_mode(hass, connection, msg):
    try:
        profile = profile_data(hass, msg["entry_id"])
        await async_set_mode(hass, profile["switch"], msg["light"], msg["mode"])
        connection.send_result(msg["id"], serialize(hass, msg["entry_id"], profile))
    except ValueError as error:
        connection.send_error(msg["id"], "invalid_mode", str(error))


async def async_setup_frontend(hass):
    async with hass.data.setdefault(FRONTEND_LOCK, asyncio.Lock()):
        if not hass.data.get(REGISTERED):
            await hass.http.async_register_static_paths([
                StaticPathConfig(STATIC_URL, str(Path(__file__).parent / "frontend"), False),
            ])
            for handler in (ws_profiles, ws_save_curve, ws_set_mode):
                websocket_api.async_register_command(hass, handler)
            hass.data[REGISTERED] = True
        if PANEL not in hass.data.get("frontend_panels", {}):
            await panel_custom.async_register_panel(
                hass, webcomponent_name="custom-lighting-panel",
                frontend_url_path=PANEL, sidebar_title="Lighting",
                sidebar_icon="mdi:chart-bell-curve-cumulative",
                module_url=f"{STATIC_URL}/lighting-panel.js?v=0.3.0",
                require_admin=True,
            )


def async_unload_frontend(hass):
    frontend.async_remove_panel(hass, PANEL)
