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
    "targets": {"brightness_pct": 75, "color_temp_kelvin": 4000, "rgb_color": [255, 200, 100]},
    "lights": [
        {"entity_id": "light.floor_lamp", "name": "Floor lamp", "state": "on", "mode": "Schedule"},
        {"entity_id": "light.ceiling", "name": "Ceiling lights", "state": "off", "mode": "Schedule"},
    ],
}
HTML = """<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1">
<style>html,body{margin:0;height:100%;font-family:system-ui}</style></head><body>
<script type="module">
import {interpolate, evaluateCurve} from "/lighting-panel.js";
window.interpolate = interpolate;
window.evaluateCurve = evaluateCurve;
window.serviceCalls = [];
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
}, callService: async (domain, action, data) => {
  serviceCalls.push({domain, action, data});
  for (const profile of profiles) for (const light of profile.lights) light.mode = "Schedule";
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
        assert page.locator("svg").count() == 1
        assert page.locator("[data-light]").count() == 2
        assert page.locator("#save").is_disabled()

        # Fields can be edited then saved in a single click, without losing focus events.
        page.locator("#point-brightness").fill("15")
        page.locator("#save").click()
        page.get_by_role("status").wait_for()
        assert page.evaluate("profiles[0].curve.points[0].brightness") == 15
        page.locator("#point-brightness").fill("17")
        page.locator("#point-brightness").press("Tab")
        page.evaluate("window.failSave = true")
        page.locator("#save").click()
        page.get_by_role("alert").wait_for()
        assert page.evaluate("panel._dirty()")
        assert page.evaluate("profiles[0].curve.points[0].brightness") == 15
        page.evaluate("window.failSave = false")
        page.locator("#save").click()
        page.get_by_role("status").wait_for()

        # Shared time/brightness handles keep the selected point's colour.
        svg = page.locator("svg")
        handle = svg.locator('[data-index="2"]')
        box, target = svg.bounding_box(), handle.bounding_box()
        page.mouse.move(target["x"] + target["width"]/2, target["y"] + target["height"]/2)
        page.mouse.down()
        page.mouse.move(box["x"] + 55 + 667/1440*(box["width"]-75),
                        box["y"] + 20 + 20/99*170, steps=8)
        page.mouse.up()
        assert page.locator("#point-time").input_value() == "11:00"
        assert page.locator("#point-brightness").input_value() == "80"
        svg.locator('[data-index="2"]').focus()
        page.keyboard.press("ArrowRight")
        assert page.locator("#point-time").input_value() == "11:15"
        page.keyboard.press("ArrowLeft")
        assert page.locator("#point-time").input_value() == "11:00"
        page.locator("#point-time").fill("11:22")
        assert page.locator("#point-time").input_value() == "11:15"
        page.locator("#add").click()
        assert page.evaluate("panel._draft.points.length") == 6
        page.locator("#remove").click()
        assert page.evaluate("panel._draft.points.length") == 5

        # The picker and exact hex input share an RGB value.
        svg.locator('[data-index="0"]').click()
        page.locator("#color-type").select_option("rgb")
        page.locator("#point-hex").fill("#ff0088")
        page.locator("#point-hex").press("Tab")
        assert page.locator("#point-rgb").input_value() == "#ff0088"
        assert page.evaluate("panel._draft.points[0].color.value") == [255, 0, 136]
        page.locator("#save").click()
        page.get_by_role("status").wait_for()
        assert page.evaluate("profiles[0].curve.points[0].color.type") == "rgb"

        # Modes and global resume preserve unsaved schedule edits; off lights stay off.
        page.locator("#point-brightness").fill("18")
        page.locator("#point-brightness").press("Tab")
        page.locator('[data-light="light.floor_lamp"]').select_option("Manual")
        page.wait_for_function("profiles[0].lights[0].mode === 'Manual' && !panel._busy")
        assert page.evaluate("panel._dirty()")
        page.locator("#resume-all").click()
        page.wait_for_function("serviceCalls.length === 1 && !panel._busy")
        assert page.evaluate("serviceCalls[0].domain") == "custom_lighting"
        assert page.evaluate("serviceCalls[0].action") == "resume_schedule"
        assert page.locator('[data-light="light.floor_lamp"]').input_value() == "Schedule"
        assert page.evaluate("profiles[0].lights[1].state") == "off"
        assert page.evaluate("panel._dirty()")
        page.locator("#save").click()
        page.get_by_role("status").wait_for()

        # Switch a single point back to Kelvin, then handle another editor's save.
        page.locator("#color-type").select_option("temperature")
        page.locator("#point-kelvin").fill("2300")
        page.locator("#point-kelvin").press("Tab")
        page.locator("h1").click()
        page.evaluate("profiles[0].revision = 'external'; profiles[0].curve.points[0].color = {type:'temperature',value:2400}")
        page.evaluate("panel._refresh()")
        page.wait_for_function("panel._conflict")
        assert page.locator("#save").is_disabled()
        assert page.evaluate("panel._draft.points[0].color.value") == 2300
        page.once("dialog", lambda dialog: dialog.accept())
        page.locator("#revert").click()
        assert page.evaluate("panel._draft.points[0].color.value") == 2400

        # Match the actual mixed RGB/Kelvin evaluator, including ties and midnight.
        rng = random.Random(29)
        for method in ("smooth", "linear"):
            for _ in range(8):
                points = [{"time": t, "brightness": rng.randint(1, 100),
                           "color": ({"type": "rgb", "value": [rng.randint(0,255) for _ in range(3)]}
                                     if rng.choice([True, False])
                                     else {"type": "temperature", "value": rng.randint(1000,10000)})}
                          for t in sorted(rng.sample(range(1440), 7))]
                curve = {"interpolation": method, "points": points}
                minutes = list(range(0, 1441, 7))
                expected = [schedule.evaluate_curve(curve, minute) for minute in minutes]
                actual = page.evaluate(
                    "({curve, minutes}) => minutes.map(m => evaluateCurve(curve, m))",
                    {"curve": curve, "minutes": minutes})
                for a, b in zip(actual, expected):
                    assert abs(a["brightness_pct"]-b["brightness_pct"]) < 1e-8
                    assert a["color_temp_kelvin"] == b["color_temp_kelvin"]
                    assert a["rgb_color"] == list(b["rgb_color"])

        # Leave a colourful draft visible for desktop/mobile screenshot review.
        svg.locator('[data-index="2"]').click()
        page.locator("#color-type").select_option("rgb")
        page.locator("#point-hex").fill("#b130ff")
        page.locator("#point-hex").press("Tab")
        svg.locator('[data-index="3"]').click()
        page.locator("#color-type").select_option("rgb")
        page.locator("#point-hex").fill("#2b92ff")
        page.locator("#point-hex").press("Tab")

        page.locator("h1").click()
        page.evaluate("panel.scrollTop = 0; panel.style.height = 'auto'; panel.style.overflow = 'visible'")
        page.screenshot(path=str(artifacts / "editor-desktop.png"), full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_timeout(150)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(artifacts / "editor-mobile.png"), full_page=True)
        assert not errors, errors
        browser.close()
    print("Browser checks passed: combined editing, RGB/Kelvin, global reset, drag, save failures, modes, conflicts, mobile layout and curve parity.")


if __name__ == "__main__":
    check()
