"""Profile and device configuration for scheduled lighting."""

import homeassistant.helpers.config_validation as cv
import probatio as vol
from homeassistant.components.light import VALID_TRANSITION

DOMAIN = "custom_lighting"
ICON_MAIN = "mdi:chart-bell-curve-cumulative"
ICON_BRIGHTNESS = "mdi:brightness-6"
ICON_COLOR_TEMP = "mdi:thermometer"
CONF_NAME = "name"
CONF_LIGHTS = "lights"
CONF_INTERVAL = "interval"
CONF_TRANSITION = "transition"
CONF_INITIAL_TRANSITION = "initial_transition"
CONF_PREFER_RGB_COLOR = "prefer_rgb_color"
CONF_SEPARATE_TURN_ON_COMMANDS = "separate_turn_on_commands"
CONF_SEND_SPLIT_DELAY = "send_split_delay"
CONF_ADAPT_DELAY = "adapt_delay"
CONF_SKIP_REDUNDANT_COMMANDS = "skip_redundant_commands"
CONF_INTERCEPT = "intercept"
CONF_MULTI_LIGHT_INTERCEPT = "multi_light_intercept"
CONF_DETECT_NON_HA_CHANGES = "detect_non_ha_changes"

ADAPT_COLOR_SWITCH = "adapt_color_switch"
ADAPT_BRIGHTNESS_SWITCH = "adapt_brightness_switch"
ATTR_ADAPTIVE_LIGHTING_MANAGER = "manager"
ATTR_ADAPT_COLOR = "adapt_color"
ATTR_ADAPT_BRIGHTNESS = "adapt_brightness"
CONF_MANUAL_CONTROL = "manual_control"
MODE_SIGNAL = f"{DOMAIN}_mode_changed"
TURNING_OFF_DELAY = 5

def int_between(low, high):
    return vol.All(vol.Coerce(int), vol.Range(min=low, max=high))

# Device-handling options remain available; scheduling is edited in the panel.
VALIDATION_TUPLES = [
    (CONF_LIGHTS, [], cv.entity_ids),
    (CONF_INTERVAL, 30, int_between(5, 300)),
    (CONF_TRANSITION, 25, VALID_TRANSITION),
    (CONF_INITIAL_TRANSITION, 1, VALID_TRANSITION),
    (CONF_PREFER_RGB_COLOR, False, bool),
    (CONF_SEPARATE_TURN_ON_COMMANDS, False, bool),
    (CONF_SEND_SPLIT_DELAY, 0, int_between(0, 10000)),
    (CONF_ADAPT_DELAY, 0, cv.positive_float),
    (CONF_SKIP_REDUNDANT_COMMANDS, True, bool),
    (CONF_INTERCEPT, True, bool),
    (CONF_MULTI_LIGHT_INTERCEPT, True, bool),
    (CONF_DETECT_NON_HA_CHANGES, False, bool),
]
