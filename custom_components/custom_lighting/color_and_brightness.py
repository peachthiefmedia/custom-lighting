"""Daily lighting curves, independent of Home Assistant and solar events."""

from bisect import bisect_right
from datetime import datetime, timedelta
from math import isfinite
from zoneinfo import ZoneInfo

DEFAULT_CURVE = {
    "brightness": [{"time": 0, "value": 10}, {"time": 420, "value": 10},
                   {"time": 540, "value": 100}, {"time": 1020, "value": 100},
                   {"time": 1320, "value": 10}],
    "temperature": [{"time": 0, "value": 2200}, {"time": 420, "value": 2200},
                    {"time": 600, "value": 5000}, {"time": 960, "value": 5000},
                    {"time": 1320, "value": 2200}],
    "interpolation": "smooth",
}


def validate_curve(curve: dict) -> dict:
    """Normalize a complete curve, rejecting missing, duplicate or invalid points."""
    if not isinstance(curve, dict) or curve.get("interpolation") not in ("linear", "smooth"):
        raise ValueError("Choose linear or smooth interpolation.")
    result = {"interpolation": curve["interpolation"]}
    for key, low, high in (("brightness", 1, 100), ("temperature", 1000, 10000)):
        points = curve.get(key)
        if not isinstance(points, list) or not 2 <= len(points) <= 48:
            raise ValueError(f"{key}: use between 2 and 48 points.")
        normalized = []
        for point in points:
            if not isinstance(point, dict):
                raise ValueError(f"{key}: invalid point.")
            time, value = point.get("time"), point.get("value")
            if (isinstance(time, bool) or not isinstance(time, int) or not 0 <= time < 1440
                or isinstance(value, bool) or not isinstance(value, (int, float))
                or not isfinite(value) or not low <= value <= high):
                raise ValueError(f"{key}: time must be 0–1439 minutes, value {low}–{high}.")
            normalized.append({"time": time, "value": float(value)})
        normalized.sort(key=lambda point: point["time"])
        if len({p["time"] for p in normalized}) != len(normalized):
            raise ValueError(f"{key}: points must have different times.")
        result[key] = normalized
    return result


def interpolate(points: list[dict], minute: float, method: str = "smooth") -> float:
    """Periodic linear or monotone cubic Hermite interpolation, without overshoot.

    The weighted harmonic tangents are the PCHIP construction. Treating neighbours
    cyclically makes the value and (for smooth curves) slope continuous at midnight.
    """
    minute %= 1440
    count = len(points)
    index = (bisect_right([p["time"] for p in points], minute) - 1) % count
    following = (index + 1) % count

    def width(i):
        return (points[(i + 1) % count]["time"] - points[i]["time"]) % 1440

    def slope(i):
        return (points[(i + 1) % count]["value"] - points[i]["value"]) / width(i)

    def tangent(i):
        before = (i - 1) % count
        left, right = slope(before), slope(i)
        if left * right <= 0:
            return 0.0
        w1, w2 = 2 * width(i) + width(before), width(i) + 2 * width(before)
        return (w1 + w2) / (w1 / left + w2 / right)

    length = width(index)
    fraction = ((minute - points[index]["time"]) % 1440) / length
    start, end = points[index]["value"], points[following]["value"]
    if method == "linear":
        return start + fraction * (end - start)
    u = fraction
    value = ((2*u**3 - 3*u**2 + 1) * start
             + (u**3 - 2*u**2 + u) * length * tangent(index)
             + (-2*u**3 + 3*u**2) * end
             + (u**3 - u**2) * length * tangent(following))
    return max(min(start, end), min(max(start, end), value))


class DailyLightSettings:
    """Evaluate the schedule using the configured local wall clock, including DST."""

    def __init__(self, curve: dict, timezone: ZoneInfo):
        self.curve = validate_curve(curve)
        self.timezone = timezone

    def brightness_and_color(self, dt: datetime) -> dict:
        """Return targets and colour representations used by the light controller."""
        from homeassistant.util.color import color_RGB_to_xy, color_temperature_to_rgb, color_xy_to_hs

        local = dt.astimezone(self.timezone)
        minute = local.hour * 60 + local.minute + local.second / 60 + local.microsecond / 60000000
        method = self.curve["interpolation"]
        kelvin = round(interpolate(self.curve["temperature"], minute, method))
        rgb = tuple(round(channel) for channel in color_temperature_to_rgb(kelvin))
        xy = color_RGB_to_xy(*rgb)
        return {
            "brightness_pct": interpolate(self.curve["brightness"], minute, method),
            "color_temp_kelvin": kelvin,
            "rgb_color": rgb,
            "xy_color": xy,
            "hs_color": color_xy_to_hs(*xy),
        }

    def get_settings(self, transition: float | None = None) -> dict:
        """Aim for the curve at the end of a device transition."""
        from homeassistant.util import dt as dt_util

        return self.brightness_and_color(dt_util.utcnow() + timedelta(seconds=transition or 0))
