"""Curve behaviour tests; run with Python's standard-library unittest runner."""

from copy import deepcopy
from datetime import UTC, datetime
import importlib.util
from pathlib import Path
import random
import sys
from types import ModuleType
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "lighting_schedule", ROOT / "custom_components/custom_lighting/color_and_brightness.py"
)
schedule = importlib.util.module_from_spec(spec)
spec.loader.exec_module(schedule)


class CurveTests(unittest.TestCase):
    def test_validation_rejects_invalid_curves(self):
        for modify in (
            lambda c: c.update(interpolation="other"),
            lambda c: c.update(points=[]),
            lambda c: c["points"].append(deepcopy(c["points"][0])),
            lambda c: c["points"][0]["color"].update(value=float("nan")),
            lambda c: c["points"][0].update(brightness=0),
            lambda c: c["points"][0].update(time=1440),
            lambda c: c["points"][0].update(time=True),
            lambda c: c["points"][0].update(time=1.5),
            lambda c: c["points"][0].update(color={"type": "rgb", "value": [256, 0, 0]}),
            lambda c: c["points"][0].update(color={"type": "rgb", "value": [True, 0, 0]}),
            lambda c: c["points"][0].update(color={"type": "rgb", "value": [1, 0]}),
        ):
            curve = deepcopy(schedule.DEFAULT_CURVE)
            modify(curve)
            with self.subTest(curve=curve), self.assertRaises(ValueError):
                schedule.validate_curve(curve)

    def test_validation_sorts_without_modifying_input(self):
        curve = deepcopy(schedule.DEFAULT_CURVE)
        curve["points"].reverse()
        normalized = schedule.validate_curve(curve)
        self.assertEqual(normalized["points"][0]["time"], 0)
        self.assertNotEqual(curve["points"], normalized["points"])

    def test_linear_wrap_and_exact_points(self):
        points = [{"time": 360, "value": 20}, {"time": 1080, "value": 100}]
        self.assertEqual(schedule.interpolate(points, 0, "linear"), 60)
        self.assertEqual(schedule.interpolate(points, 720, "linear"), 60)
        for method in ("smooth", "linear"):
            for point in points:
                self.assertEqual(schedule.interpolate(points, point["time"], method), point["value"])

    def test_smooth_has_no_overshoot_even_with_uneven_points(self):
        rng = random.Random(17)
        for _ in range(20):
            points = [{"time": time, "value": rng.uniform(1, 100)}
                      for time in sorted(rng.sample(range(1440), 12))]
            for minute in range(1440):
                before = max((p for p in points if p["time"] <= minute),
                             key=lambda p: p["time"], default=points[-1])
                following = next((p for p in points if p["time"] > minute), points[0])
                value = schedule.interpolate(points, minute)
                self.assertGreaterEqual(value + 1e-9, min(before["value"], following["value"]))
                self.assertLessEqual(value - 1e-9, max(before["value"], following["value"]))

    def test_midnight_value_and_slope_are_continuous(self):
        points = [{"time": p["time"], "value": p["brightness"]} for p in schedule.DEFAULT_CURVE["points"]]
        epsilon = 0.001
        midnight = schedule.interpolate(points, 0)
        left = schedule.interpolate(points, -epsilon)
        right = schedule.interpolate(points, epsilon)
        self.assertAlmostEqual(left, right, places=5)
        self.assertAlmostEqual((midnight-left)/epsilon, (right-midnight)/epsilon, places=4)

    def test_two_points_and_constant_values(self):
        points = [{"time": 1, "value": 40}, {"time": 1439, "value": 40}]
        for minute in (0, 1, 100, 1439, 1440, -1):
            self.assertEqual(schedule.interpolate(points, minute), 40)

    def test_local_wall_clock_in_summer_and_repeated_dst_hour(self):
        # Test the real evaluator with only colour conversion stubbed.
        color = ModuleType("homeassistant.util.color")
        color.color_temperature_to_rgb = lambda k: (255, 200, 100)
        color.color_RGB_to_xy = lambda *rgb: (0.4, 0.4)
        color.color_xy_to_hs = lambda *xy: (30, 20)
        curve = schedule.validate_curve(schedule.DEFAULT_CURVE)
        calculator = schedule.DailyLightSettings(curve, ZoneInfo("Europe/London"))
        with patch.dict(sys.modules, {"homeassistant.util.color": color}):
            summer = calculator.brightness_and_color(datetime(2026, 7, 1, 8, tzinfo=UTC))
            self.assertEqual(summer["brightness_pct"], schedule.evaluate_curve(curve, 540)["brightness_pct"])
            first = calculator.brightness_and_color(datetime(2026, 10, 25, 0, 30, tzinfo=UTC))
            second = calculator.brightness_and_color(datetime(2026, 10, 25, 1, 30, tzinfo=UTC))
            self.assertEqual(first, second)

    def test_rgb_interpolation_and_midnight_wrap(self):
        curve = schedule.validate_curve({"interpolation": "linear", "points": [
            {"time": 360, "brightness": 20, "color": {"type": "rgb", "value": [255, 0, 0]}},
            {"time": 1080, "brightness": 100, "color": {"type": "rgb", "value": [0, 0, 255]}},
        ]})
        for minute in (0, 720, 1440):
            values = schedule.evaluate_curve(curve, minute)
            self.assertEqual(values["rgb_color"], (128, 0, 128))
            self.assertIsNone(values["color_temp_kelvin"])
            self.assertEqual(values["brightness_pct"], 60)

    def test_temperature_sections_keep_native_kelvin(self):
        curve = schedule.validate_curve({"interpolation": "smooth", "points": [
            {"time": 360, "brightness": 20, "color": {"type": "temperature", "value": 2000}},
            {"time": 1080, "brightness": 100, "color": {"type": "temperature", "value": 6000}},
        ]})
        values = schedule.evaluate_curve(curve, 720)
        self.assertEqual(values["color_temp_kelvin"], 4000)
        self.assertEqual(values["rgb_color"], schedule.temperature_to_rgb(4000))

    def test_mixed_sections_convert_temperature_anchor_then_blend_rgb(self):
        curve = schedule.validate_curve({"interpolation": "smooth", "points": [
            {"time": 0, "brightness": 30, "color": {"type": "temperature", "value": 2000}},
            {"time": 720, "brightness": 70, "color": {"type": "rgb", "value": [0, 0, 255]}},
        ]})
        start = schedule.temperature_to_rgb(2000)
        expected = tuple(round((a + b) / 2) for a, b in zip(start, (0, 0, 255)))
        self.assertEqual(schedule.evaluate_curve(curve, 360)["rgb_color"], expected)
        self.assertEqual(schedule.evaluate_curve(curve, 1080)["rgb_color"], expected)
        self.assertEqual(schedule.evaluate_curve(curve, 0)["rgb_color"], start)
        self.assertIsNone(schedule.evaluate_curve(curve, 360)["color_temp_kelvin"])

    def test_deployed_separate_curves_convert_without_losing_original_anchors(self):
        legacy = {"interpolation": "smooth",
            "brightness": [{"time": 0, "value": 10}, {"time": 600, "value": 80}],
            "temperature": [{"time": 120, "value": 2200}, {"time": 720, "value": 5000}]}
        curve = schedule.validate_curve(legacy)
        self.assertEqual([p["time"] for p in curve["points"]], [0, 120, 600, 720])
        for point in legacy["brightness"]:
            self.assertEqual(schedule.evaluate_curve(curve, point["time"])["brightness_pct"], point["value"])
        for point in legacy["temperature"]:
            self.assertEqual(schedule.evaluate_curve(curve, point["time"])["color_temp_kelvin"], point["value"])
        self.assertNotIn("points", legacy)


if __name__ == "__main__":
    unittest.main()
