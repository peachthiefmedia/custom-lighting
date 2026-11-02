"""Storage, concurrency and mode filtering at the editor's HA API boundary."""

import ast
import asyncio
from copy import deepcopy
import hashlib
import json
import logging
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

from test_schedule import schedule

ROOT = Path(__file__).resolve().parents[1] / "custom_components/custom_lighting"


def api():
    tree = ast.parse((ROOT / "frontend.py").read_text(encoding="utf-8"))
    names = {"revision", "profile_data", "ws_save_curve"}
    nodes = [node for node in tree.body if getattr(node, "name", None) in names]
    for node in nodes:
        node.decorator_list = []
    scope = {"hashlib": hashlib, "json": json, "deepcopy": deepcopy, "DOMAIN": "custom_lighting",
             "validate_curve": schedule.validate_curve, "_LOGGER": logging.getLogger("test"),
             "DailyLightSettings": lambda curve, timezone: SimpleNamespace(curve=curve, timezone=timezone),
             "serialize": lambda hass, entry_id, profile: deepcopy(profile["curve"])}
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])),
                 str(ROOT / "frontend.py"), "exec"), scope)
    switch = SimpleNamespace(_schedule=SimpleNamespace(timezone="UTC"), is_on=True,
        lights=["light.manual", "light.scheduled"],
        manager=SimpleNamespace(manual_control={"light.manual": True}, reset=Mock()),
        create_context=lambda which: which, initial_transition=1,
        _update_attrs_and_maybe_adapt_lights=AsyncMock())
    profile = {"switch": switch, "curve": schedule.validate_curve(schedule.DEFAULT_CURVE),
               "curve_store": SimpleNamespace(async_save=AsyncMock()), "curve_lock": asyncio.Lock()}
    hass = SimpleNamespace(data={"custom_lighting": {"profile": profile}})
    connection = SimpleNamespace(require_admin=Mock(), send_result=Mock(), send_error=Mock())
    message = {"id": 1, "entry_id": "profile", "revision": scope["revision"](profile["curve"]),
               "curve": deepcopy(profile["curve"])}
    message["curve"]["brightness"][0]["value"] = 19
    return scope, hass, profile, connection, message


class EditorApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_save_persists_before_applying_and_preserves_manual_mode(self):
        scope, hass, profile, connection, message = api()
        order = []
        async def save(curve):
            order.append("saved")
        async def apply(**kwargs):
            order.append("applied")
        profile["curve_store"].async_save.side_effect = save
        profile["switch"]._update_attrs_and_maybe_adapt_lights.side_effect = apply
        await scope["ws_save_curve"](hass, connection, message)
        connection.send_result.assert_called_once()
        self.assertEqual(order, ["saved", "applied"])
        self.assertEqual(profile["curve"]["brightness"][0]["value"], 19)
        profile["switch"].manager.reset.assert_called_once_with("light.scheduled", reset_manual_control=False)
        self.assertTrue(profile["switch"].manager.manual_control["light.manual"])

    async def test_storage_failure_keeps_saved_and_runtime_curves(self):
        scope, hass, profile, connection, message = api()
        old = deepcopy(profile["curve"])
        previous = profile["switch"]._schedule
        profile["curve_store"].async_save.side_effect = OSError("disk full")
        with self.assertRaises(OSError):
            await scope["ws_save_curve"](hass, connection, message)
        self.assertEqual(profile["curve"], old)
        self.assertIs(profile["switch"]._schedule, previous)
        profile["switch"]._update_attrs_and_maybe_adapt_lights.assert_not_awaited()

    async def test_concurrent_saves_cannot_overwrite_a_new_revision(self):
        scope, hass, profile, connection, message = api()
        other = SimpleNamespace(require_admin=Mock(), send_result=Mock(), send_error=Mock())
        async def save(curve):
            await asyncio.sleep(0.01)
        profile["curve_store"].async_save.side_effect = save
        await asyncio.gather(scope["ws_save_curve"](hass, connection, message),
                             scope["ws_save_curve"](hass, other, {**message, "id": 2}))
        self.assertEqual(profile["curve_store"].async_save.await_count, 1)
        other.send_error.assert_called_once()
        self.assertEqual(other.send_error.call_args.args[1], "conflict")

    async def test_invalid_curve_does_not_reach_storage(self):
        scope, hass, profile, connection, message = api()
        message["curve"]["brightness"] = []
        await scope["ws_save_curve"](hass, connection, message)
        profile["curve_store"].async_save.assert_not_awaited()
        connection.send_error.assert_called_once()


if __name__ == "__main__":
    unittest.main()
