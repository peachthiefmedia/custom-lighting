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
            lambda c: c.update(brightness=[]),
            lambda c: c["brightness"].append(dict(c["brightness"][0])),
            lambda c: c["temperature"][0].update(value=float("nan")),
            lambda c: c["brightness"][0].update(value=0),
            lambda c: c["temperature"][0].update(time=1440),
            lambda c: c["brightness"][0].update(time=True),
            lambda c: c["brightness"][0].update(time=1.5),
        ):
            curve = deepcopy(schedule.DEFAULT_CURVE)
            modify(curve)
            with self.subTest(curve=curve), self.assertRaises(ValueError):
                schedule.validate_curve(curve)

    def test_validation_sorts_without_modifying_input(self):
        curve = deepcopy(schedule.DEFAULT_CURVE)
        curve["brightness"].reverse()
        normalized = schedule.validate_curve(curve)
        self.assertEqual(normalized["brightness"][0]["time"], 0)
        self.assertNotEqual(curve["brightness"], normalized["brightness"])

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
        points = schedule.DEFAULT_CURVE["brightness"]
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
            self.assertEqual(summer["brightness_pct"], schedule.interpolate(curve["brightness"], 540))
            first = calculator.brightness_and_color(datetime(2026, 10, 25, 0, 30, tzinfo=UTC))
            second = calculator.brightness_and_color(datetime(2026, 10, 25, 1, 30, tzinfo=UTC))
            self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
