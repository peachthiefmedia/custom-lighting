# Custom Lighting

Continuously interpolated daily brightness and colour-temperature curves, with
Adaptive Lighting's activation, override detection and device-handling controller.

## Install and compare

Targets Home Assistant **2026.10.4 or later**. This is a breaking update: existing
six-point settings and YAML configuration are not migrated.

1. Keep a copy of your current integration folder and settings for your comparison.
2. Copy `custom_components/custom_lighting` into Home Assistant's
   `config/custom_components` directory.
3. Restart Home Assistant. Remove the previous Custom Lighting entry in
   **Settings → Devices & services**, then add a fresh **Custom Lighting** entry.
4. Choose a profile name and lights.
5. Open **Lighting** in the sidebar. Refresh the browser if the panel is not visible.

Light groups are resolved when the profile loads. Reload the integration after
changing a group's members so the controller and per-light mode entities stay aligned.
At startup, the integration retries automatically if a referenced entity or group
has not finished setting up. An existing entity reporting unavailable does not
block setup.

The panel is available to administrators. Each light also has a normal HA
**Schedule / Manual select entity**, which can be used in dashboards and automations.

Run HCL and Custom Lighting on separate lights, or disable one controller before
enabling the other for the same lights. Two active controllers can fight each other.

## Curve editor

Brightness and temperature have **independent points** on two aligned daily charts.

- Drag a point to change its time and value; dragging time snaps to five minutes.
- Use the fields below a chart for exact minute and value input.
- Add points with **Add point** or double-click a chart.
- Remove the selected point with **Remove selected** or Delete.
- Arrow keys move a focused point; Shift increases the value step.
- **Undo** reverses edits. **Revert** loads the last saved curve.
- **Save curve** stores the draft and applies it to scheduled lights without reloading.
- Concurrent edits are detected: a stale browser cannot overwrite a newer saved curve.

Each curve accepts 2–48 distinct times from 00:00 through 23:59.
Brightness is 1–100%; temperature is 1000–10000 K and is clamped to each light's
reported limits when commands are sent. Brightness never schedules a light off.

**Smooth** uses periodic monotone cubic Hermite interpolation (PCHIP), with no
overshoot between points. **Linear ramps** interpolate directly between points.
Both wrap across midnight. The editor and backend use the same calculation.

Times follow Home Assistant's configured time zone. On a spring DST change the
curve advances with the wall clock; on the autumn repeat it repeats that hour.

Edits are drafts until saved. The preview curve changes while editing; live lights
continue to follow the saved curve. The current targets displayed above the charts
are the saved schedule's targets, before per-device limits and manual-mode filtering.

## Manual and Schedule modes

- Changing brightness or colour through HA pauses **both** curves for that light.
- Explicit brightness or colour in a turn-on command is preserved and
  switches the light to Manual mode (including scenes).
- Manual mode persists across light off/on cycles, profile enable/disable, reloads
  and Home Assistant restarts.
- Choose **Schedule** to resume at the current curve values. Off lights stay off.
- Other lights in the profile continue following their schedules independently.
- The profile's main switch enables or pauses the controller without clearing modes.
- The **Adapt Brightness** and **Adapt Color** switches independently enable those
  attributes for the entire profile.

HA service calls are detected automatically. Changes made directly through another
app or device can be detected with the optional polling setting below. Detection
uses tolerances to avoid treating ordinary device reporting differences as manual
changes. This behaviour still needs checking with your actual lights.

## Timing and device handling

Under the integration's **Configure** menu:

- **Controlled lights** changes the profile's targets.
- **Timing and device handling** controls transitions, command splitting and detection.

Defaults: update every 30 seconds, with a 25-second transition where supported,
and a 1-second transition on activation or resume. The backend aims for the value
at the end of each transition. Lights without transition support receive periodic
small updates instead. Hardware resolution, reporting and transition support affect
how smooth the physical result is.

Turn-on interception retains the original controller's ability to inject the
current targets into an initial HA light command. It uses HA's public registry
snapshot API, but still wraps HA's service handler. If interception cannot be
installed, the controller falls back to reacting to state changes.

Service targets are resolved by HA, including combined entity, device, area,
floor and label selections. Raw turn-off transition values are normalized with
HA's light validator before calculating timing windows, following current
Adaptive Lighting's handling.

Keep brightness/colour splitting and split delay for devices that need separate
commands. **Poll for changes made outside Home Assistant** is off by default:
enabling it requests a light state refresh each update cycle.

## Controls and automation

The graph editor owns the schedule. Per-light select entities own Manual/Schedule
mode, and the profile switches control enablement and adapted attributes.
There are no custom integration actions or temporary settings overrides.

```yaml
action: select.select_option
target:
  entity_id: select.living_room_floor_lamp_mode # use your actual entity ID
data:
  option: Schedule
```

Use `option: Manual` to pause a light. Use the standard `switch.turn_on` and
`switch.turn_off` actions for profile switches. Timing and device options are
changed persistently through Configure.

## Verification

```sh
python -m unittest discover -s tests -v
python -m compileall -q custom_components/custom_lighting
```

Python 3.12+ is required for the controller tests. On Windows install `tzdata` if
your Python has no time-zone database.

Optional browser checks (requires Playwright and an installed browser):

```sh
python tests/browser_check.py
```

The browser check uses Microsoft Edge on Windows, Chromium elsewhere. It checks
editing, dragging, save failures, independent modes, concurrent edits, mobile layout
and frontend/backend curve parity. Screenshots are saved under `tests/artifacts`.

Controller tests use small doubles at the Home Assistant boundary; they do not
replace a live Home Assistant test. The integration has not been run against real
bulbs in this workspace.

The CI workflow also imports the integration inside the official HA 2026.10.4
container. This container check has not been executed locally here.

Inherited web-app deployment, mirror-push and documentation-generation workflows
have been retired. Their files remain as inert placeholders rather than being
deleted.

## Credits

The retained light controller derives from
[Adaptive Lighting](https://github.com/basnijholt/adaptive-lighting), under the
Apache-2.0 licence in LICENSE.

The editor interaction design was informed by
[HCL Lighting](https://github.com/Ecronika/ha_hcl). This implementation uses SVG
without HCL's Chart.js bundle, scenarios or daylight compensation.
See THIRD_PARTY_NOTICES.md.
