"""Daily brightness and RGB/temperature keyframes, independent of Home Assistant."""

from bisect import bisect_right
from datetime import datetime, timedelta
from math import isfinite, log
from zoneinfo import ZoneInfo

MAX_POINTS = 96
DEFAULT_CURVE = {
    "points": [
        {"time": 0, "brightness": 10, "color": {"type": "temperature", "value": 2200}},
        {"time": 420, "brightness": 10, "color": {"type": "temperature", "value": 2200}},
        {"time": 600, "brightness": 100, "color": {"type": "temperature", "value": 5000}},
        {"time": 960, "brightness": 100, "color": {"type": "temperature", "value": 5000}},
        {"time": 1320, "brightness": 10, "color": {"type": "temperature", "value": 2200}},
    ],
    "interpolation": "smooth",
}


def _number(value, low, high):
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and isfinite(value) and low <= value <= high)


def _legacy_points(points, low, high):
    if not isinstance(points, list) or not 2 <= len(points) <= 48:
        raise ValueError("Invalid saved curve.")
    result = []
    for point in points:
        if (not isinstance(point, dict) or isinstance(point.get("time"), bool)
            or not isinstance(point.get("time"), int) or not 0 <= point["time"] < 1440
            or not _number(point.get("value"), low, high)):
            raise ValueError("Invalid saved curve point.")
        result.append(dict(point))
    result.sort(key=lambda point: point["time"])
    if len({point["time"] for point in result}) != len(result):
        raise ValueError("Curve points must have different times.")
    return result


def validate_curve(curve: dict) -> dict:
    """Validate shared keyframes; convert the deployed two-curve format on load."""
    if not isinstance(curve, dict) or curve.get("interpolation") not in ("linear", "smooth"):
        raise ValueError("Choose linear or smooth brightness interpolation.")
    method = curve["interpolation"]
    if "points" not in curve and "brightness" in curve and "temperature" in curve:
        brightness = _legacy_points(curve["brightness"], 1, 100)
        temperature = _legacy_points(curve["temperature"], 1000, 10000)
        times = sorted({p["time"] for p in brightness + temperature})
        curve = {"points": [
            {"time": time, "brightness": interpolate(brightness, time, method),
             "color": {"type": "temperature", "value": round(interpolate(temperature, time, method))}}
            for time in times
        ]}
    points = curve.get("points")
    if not isinstance(points, list) or not 2 <= len(points) <= MAX_POINTS:
        raise ValueError(f"Use between 2 and {MAX_POINTS} schedule points.")
    normalized = []
    for point in points:
        if not isinstance(point, dict):
            raise ValueError("Invalid schedule point.")
        time, brightness, color = point.get("time"), point.get("brightness"), point.get("color")
        if isinstance(time, bool) or not isinstance(time, int) or not 0 <= time < 1440:
            raise ValueError("Point time must be 0–1439 minutes.")
        if not _number(brightness, 1, 100):
            raise ValueError("Brightness must be 1–100%.")
        if not isinstance(color, dict):
            raise ValueError("Choose a colour or temperature for each point.")
        value = color.get("value")
        if color.get("type") == "temperature":
            if not _number(value, 1000, 10000):
                raise ValueError("Temperature must be 1000–10000 K.")
            color = {"type": "temperature", "value": round(value)}
        elif color.get("type") == "rgb":
            if (not isinstance(value, (list, tuple)) or len(value) != 3
                or any(isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= 255 for v in value)):
                raise ValueError("RGB must contain three integers from 0 to 255.")
            color = {"type": "rgb", "value": list(value)}
        else:
            raise ValueError("Choose RGB or temperature.")
        normalized.append({"time": time, "brightness": float(brightness), "color": color})
    normalized.sort(key=lambda point: point["time"])
    if len({point["time"] for point in normalized}) != len(normalized):
        raise ValueError("Points must have different times.")
    return {"interpolation": method, "points": normalized}


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


def temperature_to_rgb(kelvin: float) -> tuple[int, int, int]:
    """Approximate a white temperature for previews and mixed RGB transitions."""
    temp = kelvin / 100
    red = 255 if temp <= 66 else 329.698727446 * (temp - 60) ** -0.1332047592
    green = (99.4708025861 * log(temp) - 161.1195681661 if temp <= 66
             else 288.1221695283 * (temp - 60) ** -0.0755148492)
    blue = (255 if temp >= 66 else 0 if temp <= 19
            else 138.5177312231 * log(temp - 10) - 305.0447927307)
    return tuple(round(max(0, min(255, value))) for value in (red, green, blue))


def evaluate_curve(curve: dict, minute: float) -> dict:
    """Smooth/linear brightness and linear colour between shared daily keyframes."""
    points = curve["points"]
    minute %= 1440
    index = (bisect_right([p["time"] for p in points], minute) - 1) % len(points)
    start, end = points[index], points[(index + 1) % len(points)]
    fraction = ((minute - start["time"]) % 1440) / ((end["time"] - start["time"]) % 1440)
    a, b = start["color"], end["color"]
    if a["type"] == b["type"] == "temperature":
        kelvin = round(a["value"] + fraction * (b["value"] - a["value"]))
        rgb = temperature_to_rgb(kelvin)
    else:
        kelvin = None
        rgb_a = temperature_to_rgb(a["value"]) if a["type"] == "temperature" else a["value"]
        rgb_b = temperature_to_rgb(b["value"]) if b["type"] == "temperature" else b["value"]
        rgb = tuple(round(x + fraction * (y - x)) for x, y in zip(rgb_a, rgb_b))
    brightness = [{"time": p["time"], "value": p["brightness"]} for p in points]
    return {"brightness_pct": interpolate(brightness, minute, curve["interpolation"]),
            "color_temp_kelvin": kelvin, "rgb_color": rgb}


class DailyLightSettings:
    """Evaluate schedules using Home Assistant's local wall clock, including DST."""

    def __init__(self, curve: dict, timezone: ZoneInfo):
        self.curve = validate_curve(curve)
        self.timezone = timezone

    def brightness_and_color(self, dt: datetime) -> dict:
        from homeassistant.util.color import color_RGB_to_xy, color_xy_to_hs

        local = dt.astimezone(self.timezone)
        minute = local.hour * 60 + local.minute + local.second / 60 + local.microsecond / 60000000
        settings = evaluate_curve(self.curve, minute)
        xy = color_RGB_to_xy(*settings["rgb_color"])
        return {**settings, "xy_color": xy, "hs_color": color_xy_to_hs(*xy)}

    def get_settings(self, transition: float | None = None) -> dict:
        from homeassistant.util import dt as dt_util

        return self.brightness_and_color(dt_util.utcnow() + timedelta(seconds=transition or 0))
