"""Headless editor checks with a mock HA API, plus Python/JS curve parity.

Requires Playwright and a browser. On Windows this uses installed Microsoft Edge.
Screenshots are written to tests/artifacts for review.
"""

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import random
import sys

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "schedule", ROOT / "custom_components/custom_lighting/color_and_brightness.py")
schedule = importlib.util.module_from_spec(spec)
spec.loader.exec_module(schedule)
EDITOR = (ROOT / "custom_components/custom_lighting/frontend/lighting-panel.js").read_text(encoding="utf-8")

profile = {
    "id": "living-room", "name": "Living room", "enabled": True,
    "curve": schedule.DEFAULT_CURVE, "revision": "first", "timezone": "Europe/London",
    "targets": {"brightness_pct": 75, "color_temp_kelvin": 4000},
    "lights": [
        {"entity_id": "light.floor_lamp", "name": "Floor lamp", "state": "on", "mode": "Schedule"},
        {"entity_id": "light.ceiling", "name": "Ceiling lights", "state": "off", "mode": "Schedule"},
    ],
}
HTML = """<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1">
<style>html,body{margin:0;height:100%;font-family:system-ui}</style></head><body>
<script type="module">
import {interpolate} from "/lighting-panel.js";
window.interpolate = interpolate;
window.profiles = [PROFILE];
window.calls = [];
window.failSave = false;
const panel = document.createElement("custom-lighting-panel");
panel.hass = {config: {time_zone:"Europe/London"}, callWS:async msg => {
  calls.push(structuredClone(msg));
  if (msg.type === "custom_lighting/profiles") return structuredClone(profiles);
  const profile = profiles.find(p => p.id === msg.entry_id);
  if (msg.type === "custom_lighting/save_curve") {
    if (window.failSave) throw new Error("Test storage failure");
    profile.curve = structuredClone(msg.curve);
    profile.revision += "-saved";
  }
  if (msg.type === "custom_lighting/set_mode")
    profile.lights.find(l => l.entity_id === msg.light).mode = msg.mode;
  return structuredClone(profile);
}};
document.body.append(panel);
window.panel = panel;
</script></body></html>""".replace("PROFILE", json.dumps(profile))


def check():
    artifacts = ROOT / "tests/artifacts"
    artifacts.mkdir(exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(**({"channel": "msedge"} if sys.platform == "win32" else {}))
        context = browser.new_context(viewport={"width": 1280, "height": 1100}, device_scale_factor=1)
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("http://lighting.test/**", lambda route: route.fulfill(
            body=EDITOR if route.request.url.endswith(".js") else HTML,
            content_type="text/javascript" if route.request.url.endswith(".js") else "text/html"))
        page.goto("http://lighting.test")
        page.locator("svg").first.wait_for()
        assert page.locator("svg").count() == 2
        assert page.locator("[data-light]").count() == 2
        assert page.locator("#save").is_disabled()

        # Numeric edits, save acknowledgement and failed saves retaining the draft.
        page.locator('[data-value="brightness"]').fill("15")
        page.locator('[data-value="brightness"]').press("Tab")
        assert page.locator("#save").is_enabled()
        page.locator("#save").click()
        page.get_by_role("status").wait_for()
        assert page.evaluate("profiles[0].curve.brightness[0].value") == 15
        page.locator('[data-value="brightness"]').fill("17")
        page.locator('[data-value="brightness"]').press("Tab")
        page.evaluate("window.failSave = true")
        page.locator("#save").click()
        page.get_by_role("alert").wait_for()
        assert page.evaluate("panel._dirty()")
        assert page.evaluate("profiles[0].curve.brightness[0].value") == 15
        page.evaluate("window.failSave = false")
        page.locator("#save").click()
        page.get_by_role("status").wait_for()

        # A pointer drag changes both time and value of the selected point.
        svg = page.locator('svg[data-channel="brightness"]')
        handle = svg.locator('[data-index="2"]')
        box, target = svg.bounding_box(), handle.bounding_box()
        page.mouse.move(target["x"] + target["width"]/2, target["y"] + target["height"]/2)
        page.mouse.down()
        page.mouse.move(box["x"] + 55 + 600/1440*(box["width"]-75),
                        box["y"] + 20 + 20/99*180, steps=8)
        page.mouse.up()
        assert page.locator('[data-time="brightness"]').input_value() == "10:00"
        assert page.locator('[data-value="brightness"]').input_value() == "80"
        page.locator('[data-add="brightness"]').click()
        assert page.evaluate("panel._draft.brightness.length") == 6
        page.locator('[data-remove="brightness"]').click()
        assert page.evaluate("panel._draft.brightness.length") == 5

        # Per-light mode updates do not discard an unsaved curve.
        page.locator('[data-light="light.floor_lamp"]').select_option("Manual")
        page.wait_for_function("profiles[0].lights[0].mode === 'Manual' && !panel._busy")
        assert page.evaluate("panel._dirty()")
        assert page.locator('[data-light="light.ceiling"]').input_value() == "Schedule"
        page.locator("#save").click()
        page.get_by_role("status").wait_for()

        # Another editor changing the curve causes a conflict, preserving our draft.
        page.locator('[data-value="temperature"]').fill("2300")
        page.locator('[data-value="temperature"]').press("Tab")
        page.locator("h1").click()
        page.evaluate("profiles[0].revision = 'external'; profiles[0].curve.temperature[0].value = 2400")
        page.evaluate("panel._refresh()")
        page.wait_for_function("panel._conflict")
        assert page.locator("#save").is_disabled()
        assert page.evaluate("panel._draft.temperature[0].value") == 2300
        page.once("dialog", lambda dialog: dialog.accept())
        page.locator("#revert").click()
        assert page.evaluate("panel._draft.temperature[0].value") == 2400

        # The plotted frontend curve must equal the backend's output.
        rng = random.Random(29)
        for method in ("smooth", "linear"):
            for _ in range(8):
                points = [{"time": t, "value": rng.randint(1, 100)}
                          for t in sorted(rng.sample(range(1440), 7))]
                minutes = list(range(0, 1441, 7))
                expected = [schedule.interpolate(points, minute, method) for minute in minutes]
                actual = page.evaluate(
                    "({points, minutes, method}) => minutes.map(m => interpolate(points, m, method))",
                    {"points": points, "minutes": minutes, "method": method})
                assert all(abs(a-b) < 1e-8 for a, b in zip(actual, expected))

        page.evaluate("panel.scrollTop = 0; panel.style.height = 'auto'; panel.style.overflow = 'visible'")
        page.screenshot(path=str(artifacts / "editor-desktop.png"), full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_timeout(150)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(artifacts / "editor-mobile.png"), full_page=True)
        assert not errors, errors
        browser.close()
    print("Browser checks passed: editing, drag, save/failure, modes, conflicts, mobile layout and curve parity.")


if __name__ == "__main__":
    check()
