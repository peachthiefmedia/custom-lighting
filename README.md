# Custom Lighting

<img src="custom_components/custom_lighting/brand/icon.png" alt="Custom Lighting" width="96" height="96">

Schedule brightness, RGB colour and white colour temperature with a visual 24-hour curve
editor. Custom Lighting retains Adaptive Lighting's light-activation, manual
override and device-handling controller; it no longer uses its solar scheduler.

## Supported features

| Feature | Current behaviour |
| --- | --- |
| Schedule | A repeating daily profile with shared brightness and colour points |
| Editing | Drag points, enter exact values, add/remove points, undo, save and revert |
| Interpolation | Smooth or linear brightness; linear temperature/RGB blends, including across midnight |
| Lights | Dimmable lights; tunable-white lights; approximate white through RGB on colour-capable lights |
| Profiles | Multiple profiles, with each light assigned to one profile |
| Manual control | Per-light Manual/Schedule modes, preserved across off/on cycles and restarts |
| Resume | One global action returns every controlled light to Schedule mode |
| Activation | Apply the current targets when a scheduled light turns on |
| Device handling | Capability/range checks, supported transitions and optional split commands |

On/off-only lights cannot follow brightness or temperature curves. The integration
does not automatically switch lights on or off. White-only lights follow brightness
during RGB sections; RGB colour requires a colour-capable light.

## What changed from the old fork

- The graph replaces the six fixed time-point fields.
- Solar position, sunrise/sunset settings and solar brightness modes are removed.
- Sleep-mode settings and the inherited Adaptive Lighting configuration guide are removed.
- The custom `apply`, `set_manual_control` and `change_switch_settings` actions are
  removed. Use the graph, normal HA select entities and profile switches instead.
- Temporary settings overrides are removed; device options are saved through Configure.
- There is no YAML configuration or migration from the original six-point entries.

The schedule currently repeats every day. Weekday/weekend schedules, HCL scenarios
and lux/daylight compensation are not implemented.

## Install and compare

Targets Home Assistant **2026.10.4 or later**. This is a breaking update: existing
six-point settings and YAML configuration are not migrated.

### HACS

Add `https://github.com/peachthiefmedia/custom-lighting` as an **Integration** in
[HACS custom repositories](https://www.hacs.dev/docs/faq/custom_repositories/),
then download Custom Lighting. If it is already installed, update or redownload
this repository in HACS.

For the current test update, use the repository's **main** branch; no new release
tag has been published. Restart HA after downloading and refresh your browser.
When upgrading from the original six-point fork, remove its previous entry and
add a fresh entry. Existing 0.2 profiles can be kept.

This README is also the integration's HACS information page: `hacs.json` sets
`render_readme: true`. There is no separate inherited Adaptive Lighting information
page to configure.

The integration includes local brand icons for Home Assistant. HACS on HA 2026.10
may still show its placeholder due to its upstream image lookup limitation;
see [HACS issue 5402](https://github.com/hacs/integration/issues/5402).

### Manual installation and setup

1. Keep a copy of your current integration folder and settings for your comparison.
2. Copy `custom_components/custom_lighting` into Home Assistant's
   `config/custom_components` directory.
3. Restart Home Assistant. For an original six-point entry, remove it in
   **Settings → Devices & services**, then add a fresh **Custom Lighting** entry.
   Keep existing 0.2 profiles.
4. For a new entry, choose a profile name and lights.
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

Brightness and colour share points on one daily chart. The coloured strip previews
the colour schedule. Select a point to choose Kelvin temperature or RGB using a
colour picker or hex value.

- Drag a point to change its time and value; times snap to 15-minute increments.
- Use the fields below the chart for time, brightness and colour input.
- Add points with **Add point** or double-click a chart.
- Remove the selected point with **Remove selected** or Delete.
- Arrow keys move a focused point; Shift increases the value step.
- **Undo** reverses edits. **Revert** loads the last saved curve.
- **Save curve** stores the draft and applies it to scheduled lights without reloading.
- Concurrent edits are detected: a stale browser cannot overwrite a newer saved curve.

Each schedule accepts 2–96 distinct times from 00:00 through 23:59.
New or edited times use 15-minute increments, ending at 23:45; existing saved
times are retained until edited. Interpolation continues between those points.
Brightness is 1–100%; temperature is 1000–10000 K and is clamped to each light's
reported limits when commands are sent. Brightness never schedules a light off.

For brightness, **Smooth** uses periodic monotone cubic Hermite interpolation
(PCHIP), with no overshoot; **Linear ramps** interpolate directly between points.
Between two temperature points, Kelvin is interpolated and sent natively where
supported. Sections with an RGB endpoint blend RGB channels, converting any
temperature endpoint to approximate RGB. All sections wrap across midnight.
The editor and backend use the same calculation.

Existing 0.2 curves are converted using the union of their brightness and
temperature point times. Original anchor values are retained, but intermediate
ramps can change: review the preview before saving the combined schedule.

Times follow Home Assistant's configured time zone. On a spring DST change the
curve advances with the wall clock; on the autumn repeat it repeats that hour.

Edits are drafts until saved. The preview curve changes while editing; live lights
continue to follow the saved curve. The current targets displayed above the charts
are the saved schedule's targets, before per-device limits and manual-mode filtering.

## Manual and Schedule modes

- Changing brightness or colour through HA pauses the schedule for that light.
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
and a 1-second transition on activation. Resuming Schedule mode uses the normal
configured transition (25 seconds by default). The backend aims for the value
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
The **Resume all schedules** panel button and `custom_lighting.resume_schedule`
action return every controlled light to Schedule mode. Current targets are applied
to lights that are on; off lights stay off and paused profiles remain paused.
Lights already in Schedule mode keep their existing fades and receive no reset commands.
There are no temporary settings overrides.

Use this action in an automation to reset manual overrides periodically:

```yaml
action: custom_lighting.resume_schedule
```

To resume just one light:

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

## Help and issue reports

Use this repository's [issues](https://github.com/peachthiefmedia/custom-lighting/issues)
for this fork. Include the HA version, Custom Lighting version or commit, light
integration and model, relevant curve/device settings, and steps to reproduce.
For the editor, include any browser console error. The controls and configuration
of upstream Adaptive Lighting do not describe this version of Custom Lighting.

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
