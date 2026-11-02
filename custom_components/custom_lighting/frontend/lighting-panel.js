/* Custom Lighting curve editor. No external assets or build step required.
 * Interaction design informed by Ecronika/ha_hcl (MIT); see THIRD_PARTY_NOTICES.md.
 */
const CHANNELS = {
  brightness: {label: "Brightness", unit: "%", min: 1, max: 100, step: 1, color: "#e6a13a"},
  temperature: {label: "Colour temperature", unit: "K", min: 1000, max: 10000, step: 50, color: "#54a9db"},
};
const clone = value => JSON.parse(JSON.stringify(value));
const escapeHtml = value => String(value).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const clock = minute => String(Math.floor(minute / 60)).padStart(2, "0") + ":" + String(minute % 60).padStart(2, "0");

// Periodic PCHIP: exactly the same construction as color_and_brightness.py.
export function interpolate(points, minute, method = "smooth") {
  minute = ((minute % 1440) + 1440) % 1440;
  const n = points.length;
  let index = points.findIndex(p => p.time > minute) - 1;
  if (index === -2) index = n - 1;
  if (index === -1) index = n - 1;
  const next = (index + 1) % n;
  const width = i => (points[(i + 1) % n].time - points[i].time + 1440) % 1440;
  const slope = i => (points[(i + 1) % n].value - points[i].value) / width(i);
  const tangent = i => {
    const before = (i + n - 1) % n, left = slope(before), right = slope(i);
    if (left * right <= 0) return 0;
    const w1 = 2 * width(i) + width(before), w2 = width(i) + 2 * width(before);
    return (w1 + w2) / (w1 / left + w2 / right);
  };
  const length = width(index), u = ((minute - points[index].time + 1440) % 1440) / length;
  const start = points[index].value, end = points[next].value;
  if (method === "linear") return start + u * (end - start);
  const value = (2*u**3 - 3*u**2 + 1)*start + (u**3 - 2*u**2 + u)*length*tangent(index)
    + (-2*u**3 + 3*u**2)*end + (u**3 - u**2)*length*tangent(next);
  return Math.max(Math.min(start, end), Math.min(Math.max(start, end), value));
}

export class CustomLightingPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({mode: "open"});
    this._profiles = [];
    this._selected = {brightness: 0, temperature: 0};
    this._history = [];
    this._busy = false;
    this._error = "";
    this._message = "";
  }
  set hass(value) {
    this._hass = value;
    if (this.isConnected && !this._loaded && !this._loading) this._refresh();
  }
  set narrow(value) { this._narrow = value; }
  set panel(value) { this._panel = value; }
  connectedCallback() {
    if (this._hass && !this._loaded) this._refresh();
    this._timer = setInterval(() => this._refresh(), 10000);
    this._beforeUnload = event => {
      if (this._dirty()) { event.preventDefault(); event.returnValue = ""; }
    };
    window.addEventListener("beforeunload", this._beforeUnload);
    this._resizeObserver = new ResizeObserver(() => this._drawAll());
    this._resizeObserver.observe(this);
    this.render();
  }
  disconnectedCallback() {
    clearInterval(this._timer);
    window.removeEventListener("beforeunload", this._beforeUnload);
    this._resizeObserver?.disconnect();
    this._drag = null;
  }
  _dirty() { return this._draft && JSON.stringify(this._draft) !== JSON.stringify(this._saved); }
  async _refresh() {
    if (!this._hass || this._loading || this._drag || this._busy) return;
    this._loading = true;
    try {
      const profiles = await this._hass.callWS({type: "custom_lighting/profiles"});
      if (this._drag || this._busy) return;
      this._profiles = profiles;
      this._loaded = true;
      const profile = profiles.find(p => p.id === this._id) || profiles[0];
      if (profile) {
        if (this._id !== profile.id) {
          if (this._dirty()) {
            this._error = "This profile is no longer loaded. Your draft is kept; reload its integration to continue.";
            this.render();
            return;
          }
          this._loadProfile(profile);
        } else if (profile.revision !== this._revision) {
          if (this._dirty()) this._conflict = true;
          else this._loadProfile(profile);
        }
      }
      this._error = "";
      // Do not replace an active input or pointer target during a background poll.
      const active = this.shadowRoot.activeElement;
      if (!active?.closest(".point-editor")) this.render();
      else this._drawAll();
    } catch (error) {
      this._error = error.message || String(error);
      this.render();
    } finally { this._loading = false; }
  }
  _loadProfile(profile) {
    this._id = profile.id;
    this._saved = clone(profile.curve);
    this._draft = clone(profile.curve);
    this._revision = profile.revision;
    this._selected = {brightness: 0, temperature: 0};
    this._history = [];
    this._conflict = false;
    this._message = "";
  }
  _remember() {
    this._history.push(clone(this._draft));
    if (this._history.length > 50) this._history.shift();
    this._message = "";
  }
  _now() {
    const profile = this._profiles.find(p => p.id === this._id);
    const parts = new Intl.DateTimeFormat("en-GB", {
      timeZone: profile?.timezone || this._hass?.config?.time_zone || "UTC",
      hour: "2-digit", minute: "2-digit", hourCycle: "h23",
    }).formatToParts(new Date());
    return Number(parts.find(p => p.type === "hour").value) * 60
      + Number(parts.find(p => p.type === "minute").value);
  }
  _coordinate(event, key) {
    const box = this.shadowRoot.querySelector('svg[data-channel="' + key + '"]').getBoundingClientRect();
    const config = CHANNELS[key];
    const x = Math.max(0, Math.min(1, (event.clientX - box.left - 55) / (box.width - 75)));
    const y = Math.max(0, Math.min(1, (event.clientY - box.top - 20) / 180));
    return {
      time: Math.min(1439, Math.round(x * 1440 / 5) * 5),
      value: Math.max(config.min, Math.min(config.max,
        Math.round((config.max - y * (config.max - config.min)) / config.step) * config.step)),
    };
  }
  _movePoint(key, point, time, value) {
    const points = this._draft[key];
    if (points.some(p => p !== point && p.time === time)) return false;
    point.time = time;
    point.value = value;
    points.sort((a, b) => a.time - b.time);
    this._selected[key] = points.indexOf(point);
    return true;
  }
  _add(key, coordinate) {
    const points = this._draft[key];
    if (points.length >= 48) { this._error = "Use at most 48 points per curve."; this.render(); return; }
    let time = coordinate?.time ?? this._now();
    while (points.some(p => p.time === time)) time = (time + 5) % 1440;
    this._remember();
    const point = {time, value: coordinate?.value ?? Math.round(interpolate(points, time, this._draft.interpolation))};
    points.push(point);
    points.sort((a, b) => a.time - b.time);
    this._selected[key] = points.indexOf(point);
    this.render();
  }
  _remove(key) {
    if (this._draft[key].length <= 2) return;
    this._remember();
    this._draft[key].splice(this._selected[key], 1);
    this._selected[key] = Math.min(this._selected[key], this._draft[key].length - 1);
    this.render();
  }
  _drawAll() { if (this._draft) for (const key of Object.keys(CHANNELS)) this._draw(key); }
  _draw(key) {
    const svg = this.shadowRoot.querySelector('svg[data-channel="' + key + '"]');
    if (!svg || !this._draft) return;
    const config = CHANNELS[key], points = this._draft[key];
    const width = svg.getBoundingClientRect().width || 900;
    svg.setAttribute("viewBox", "0 0 " + width + " 240");
    const x = time => 55 + time / 1440 * (width - 75);
    const y = value => 20 + (config.max - value) / (config.max - config.min) * 180;
    let grid = "";
    for (let hour = 0; hour <= 24; hour += width < 500 ? 6 : 3) {
      grid += '<line x1="' + x(hour*60) + '" x2="' + x(hour*60) + '" y1="20" y2="200" class="grid"/>';
      grid += '<text x="' + x(hour*60) + '" y="225" text-anchor="middle">' + String(hour).padStart(2,"0") + ':00</text>';
    }
    for (let i = 0; i <= 4; i++) {
      const value = config.min + (config.max - config.min) * i/4, cy = y(value);
      grid += '<line x1="55" x2="' + (width - 20) + '" y1="' + cy + '" y2="' + cy + '" class="grid"/>';
      grid += '<text x="45" y="' + (cy+4) + '" text-anchor="end">' + Math.round(value) + '</text>';
    }
    let path = "";
    for (let time = 0; time <= 1440; time += 3) {
      path += (time ? " L" : "M") + x(time).toFixed(2) + "," + y(interpolate(points, time, this._draft.interpolation)).toFixed(2);
    }
    const handles = points.map((p, i) => '<circle cx="' + x(p.time) + '" cy="' + y(p.value)
      + '" r="' + (i === this._selected[key] ? 7 : 5) + '" class="handle ' + (i === this._selected[key] ? "selected" : "")
      + '"/><circle data-index="' + i + '" cx="' + x(p.time) + '" cy="' + y(p.value)
      + '" r="16" class="hit" tabindex="0" role="slider" aria-label="' + config.label + ' at ' + clock(p.time) + '" aria-valuemin="' + config.min
      + '" aria-valuemax="' + config.max + '" aria-valuenow="' + p.value + '"><title>' + clock(p.time) + ' · ' + p.value + " " + config.unit + '</title></circle>').join("");
    svg.innerHTML = grid + '<path d="' + path + '" fill="none" stroke="' + config.color + '" stroke-width="3"/>'
      + '<line class="now" x1="' + x(this._now()) + '" x2="' + x(this._now()) + '" y1="15" y2="205"/>'
      + handles;
  }
  _bindChart(key) {
    const svg = this.shadowRoot.querySelector('svg[data-channel="' + key + '"]');
    svg.addEventListener("pointerdown", event => {
      if (this._busy || event.button !== 0) return;
      const handle = event.target.closest("[data-index]");
      if (!handle) return;
      event.preventDefault();
      const index = Number(handle.dataset.index);
      this._selected[key] = index;
      this._remember();
      this._drag = {key, point: this._draft[key][index]};
      svg.setPointerCapture(event.pointerId);
      this._draw(key);
    });
    svg.addEventListener("pointermove", event => {
      if (!this._drag || this._drag.key !== key) return;
      const coordinate = this._coordinate(event, key);
      this._movePoint(key, this._drag.point, coordinate.time, coordinate.value);
      this._draw(key);
      const section = svg.closest("section"), point = this._drag.point;
      section.querySelector('input[type="time"]').value = clock(point.time);
      section.querySelector('input[type="number"]').value = point.value;
    });
    const endDrag = () => {
      if (!this._drag) return;
      this._drag = null;
      this.render();
    };
    svg.addEventListener("pointerup", endDrag);
    svg.addEventListener("pointercancel", endDrag);
    svg.addEventListener("lostpointercapture", endDrag);
    svg.addEventListener("dblclick", event => {
      if (!this._busy && !event.target.closest("[data-index]")) this._add(key, this._coordinate(event, key));
    });
    svg.addEventListener("keydown", event => {
      const handle = event.target.closest("[data-index]");
      if (!handle || this._busy) return;
      const index = Number(handle.dataset.index), point = this._draft[key][index], config = CHANNELS[key];
      this._selected[key] = index;
      if (event.key === "Delete" || event.key === "Backspace") {
        event.preventDefault(); this._remove(key); return;
      }
      if (!["ArrowLeft","ArrowRight","ArrowUp","ArrowDown"].includes(event.key)) return;
      event.preventDefault();
      this._remember();
      const time = Math.max(0, Math.min(1439, point.time + (event.key === "ArrowLeft" ? -5 : event.key === "ArrowRight" ? 5 : 0)));
      const value = Math.max(config.min, Math.min(config.max, point.value
        + (event.key === "ArrowUp" ? config.step : event.key === "ArrowDown" ? -config.step : 0) * (event.shiftKey ? 10 : 1)));
      this._movePoint(key, point, time, value);
      this.render();
      this.shadowRoot.querySelector('svg[data-channel="' + key + '"] [data-index="' + this._selected[key] + '"]')?.focus();
    });
  }
  async _save() {
    this._busy = true; this._error = ""; this.render();
    try {
      const profile = await this._hass.callWS({
        type: "custom_lighting/save_curve", entry_id: this._id,
        revision: this._revision, curve: this._draft,
      });
      this._profiles = this._profiles.map(p => p.id === profile.id ? profile : p);
      this._loadProfile(profile);
      this._message = "Saved. Scheduled lights now follow this curve.";
    } catch (error) { this._error = error.message || String(error); }
    finally { this._busy = false; this.render(); }
  }
  async _mode(light, mode) {
    this._busy = true; this._error = ""; this.render();
    try {
      const profile = await this._hass.callWS({
        type: "custom_lighting/set_mode", entry_id: this._id, light, mode,
      });
      this._profiles = this._profiles.map(p => p.id === profile.id ? profile : p);
    } catch (error) { this._error = error.message || String(error); }
    finally { this._busy = false; this.render(); }
  }
  render() {
    const root = this.shadowRoot;
    const profile = this._profiles.find(p => p.id === this._id);
    const disabled = this._busy ? "disabled" : "";
    const chart = key => {
      const config = CHANNELS[key], point = this._draft[key][this._selected[key]];
      return '<section><div class="chart-title"><h2>' + config.label + '</h2><span>' + this._draft[key].length
        + ' points · ' + config.unit + '</span></div><svg data-channel="' + key
        + '" viewBox="0 0 1000 240" role="group" aria-label="' + config.label + ' daily curve"></svg>'
        + '<div class="point-editor"><label>Time<input data-time="' + key + '" type="time" value="' + clock(point.time)
        + '" ' + disabled + '></label><label>' + config.label + ' (' + config.unit + ')<input data-value="' + key
        + '" type="number" min="' + config.min + '" max="' + config.max + '" step="1" value="' + point.value + '" ' + disabled + '></label>'
        + '<button data-add="' + key + '" ' + disabled + '>Add point</button><button data-remove="' + key + '" '
        + (this._draft[key].length <= 2 || this._busy ? "disabled" : "") + '>Remove selected</button></div></section>';
    };
    root.innerHTML = '<style>' + STYLES + '</style><main><button id="menu" aria-label="Open Home Assistant sidebar">☰</button><header><div><p class="eyebrow">CUSTOM LIGHTING</p><h1>Daily lighting curves</h1>'
      + '<p>Smooth light throughout the day. Drag points or enter exact values.</p></div><label>Profile<select id="profile" '
      + disabled + '>' + this._profiles.map(p => '<option value="' + escapeHtml(p.id) + '" '
        + (p.id === this._id ? "selected" : "") + '>' + escapeHtml(p.name) + '</option>').join("") + '</select></label></header>'
      + (this._error ? '<div class="error" role="alert">' + escapeHtml(this._error) + '</div>' : "")
      + (this._message ? '<div class="success" role="status">' + escapeHtml(this._message) + '</div>' : "")
      + (profile && this._draft ? '<div class="status"><span class="badge">' + (profile.enabled ? "Enabled" : "Profile paused")
        + '</span><span>Now ' + clock(this._now()) + ' · ' + Math.round(profile.targets.brightness_pct) + '% · '
        + profile.targets.color_temp_kelvin + ' K</span><span>' + escapeHtml(profile.timezone) + '</span></div>'
        + (!profile.enabled ? '<p class="notice">This profile is paused. Enable its main switch in Home Assistant to apply the schedule.</p>' : "")
        + (this._conflict ? '<p class="notice">The saved curve changed elsewhere. Your draft is kept. Revert to load the latest version.</p>' : "")
        + '<div class="toolbar"><label>Curve shape<select id="interpolation" ' + disabled + '><option value="smooth" '
        + (this._draft.interpolation === "smooth" ? "selected" : "") + '>Smooth</option><option value="linear" '
        + (this._draft.interpolation === "linear" ? "selected" : "") + '>Linear ramps</option></select></label><span class="draft">'
        + (this._dirty() ? "Unsaved changes" : "Saved curve") + '</span><button id="undo" '
        + (!this._history.length || this._busy ? "disabled" : "") + '>Undo</button><button id="revert" ' + disabled
        + '>Revert</button><button id="save" class="primary" ' + (!this._dirty() || this._busy || this._conflict ? "disabled" : "")
        + '>Save curve</button></div>' + chart("brightness") + chart("temperature")
        + '<p class="hint">Double-click a chart to add a point. Arrow keys adjust a selected point; Delete removes it. Curves wrap across midnight. Editing is a draft until saved.</p>'
        + '<section><h2>Light control</h2><p>Manual changes pause both curves for that light. Modes survive off/on cycles and restarts.</p><div class="lights">'
        + profile.lights.map(light => '<div class="light"><div><strong>' + escapeHtml(light.name) + '</strong><small>'
          + escapeHtml(light.entity_id) + ' · ' + escapeHtml(light.state) + '</small></div><label>Mode<select data-light="'
          + escapeHtml(light.entity_id) + '" ' + disabled + '><option ' + (light.mode === "Schedule" ? "selected" : "")
          + '>Schedule</option><option ' + (light.mode === "Manual" ? "selected" : "") + '>Manual</option></select></label></div>').join("")
        + '</div><p class="hint">Schedule resumes at the current curve values. Lights that are off stay off.</p></section>'
        : '<section><h2>' + (this._loaded ? "No lighting profiles loaded" : "Loading lighting profiles…")
          + '</h2><p>Add Custom Lighting in Settings → Devices &amp; services, then choose the lights to control.</p></section>')
      + '</main>';
    root.querySelector("#menu").addEventListener("click", () => this.dispatchEvent(new CustomEvent("hass-toggle-menu", {bubbles:true, composed:true})));
    root.querySelector("#profile")?.addEventListener("change", event => {
      if (this._dirty() && !window.confirm("Discard unsaved changes and switch profiles?")) { this.render(); return; }
      this._loadProfile(this._profiles.find(p => p.id === event.target.value)); this.render();
    });
    if (!profile || !this._draft) return;
    root.querySelector("#interpolation").addEventListener("change", event => {
      this._remember(); this._draft.interpolation = event.target.value; this.render();
    });
    root.querySelector("#save").addEventListener("click", () => this._save());
    root.querySelector("#undo").addEventListener("click", () => {
      if (this._history.length) { this._draft = this._history.pop(); this._selected = {brightness: 0, temperature: 0}; this.render(); }
    });
    root.querySelector("#revert").addEventListener("click", () => {
      if (this._dirty() && !window.confirm("Discard unsaved curve changes?")) return;
      this._loadProfile(profile); this.render();
    });
    for (const key of Object.keys(CHANNELS)) {
      this._bindChart(key);
      root.querySelector('[data-add="' + key + '"]').addEventListener("click", () => this._add(key));
      root.querySelector('[data-remove="' + key + '"]').addEventListener("click", () => this._remove(key));
      const update = () => {
        const timeText = root.querySelector('[data-time="' + key + '"]').value;
        const valueInput = root.querySelector('[data-value="' + key + '"]');
        const value = Number(valueInput.value), [hours, minutes] = timeText.split(":").map(Number);
        const time = hours * 60 + minutes, config = CHANNELS[key];
        if (!/^\d{2}:\d{2}$/.test(timeText) || !valueInput.value || !Number.isFinite(value)
            || value < config.min || value > config.max || time < 0 || time >= 1440) {
          this._error = "Enter a valid time and a value within the displayed limits."; this.render(); return;
        }
        this._remember();
        if (!this._movePoint(key, this._draft[key][this._selected[key]], time, value))
          this._error = "Two points cannot have the same time.";
        else this._error = "";
        this.render();
      };
      root.querySelector('[data-time="' + key + '"]').addEventListener("change", update);
      root.querySelector('[data-value="' + key + '"]').addEventListener("change", update);
    }
    root.querySelectorAll("[data-light]").forEach(select => {
      select.addEventListener("change", () => this._mode(select.dataset.light, select.value));
    });
    this._drawAll();
  }
}

const STYLES = `
  :host { display:block; height:100%; overflow:auto; background:var(--primary-background-color,#f4f6f8); color:var(--primary-text-color,#18242c); font-family:var(--paper-font-body1_-_font-family,system-ui,sans-serif); }
  * { box-sizing:border-box; } main { max-width:1120px; padding:32px 28px 60px; margin:auto; }
  header { display:flex; justify-content:space-between; align-items:center; gap:24px; margin-bottom:22px; }
  h1 { font-size:30px; letter-spacing:-.7px; margin:5px 0 8px; } h2 { font-size:18px; margin:0; font-weight:600; }
  p { line-height:1.5; color:var(--secondary-text-color,#63727c); margin:8px 0; } .eyebrow { font-size:11px; font-weight:700; letter-spacing:2px; color:var(--primary-color,#347f91); }
  section { padding:22px; margin:18px 0; background:var(--card-background-color,#fff); border:1px solid var(--divider-color,#dde4e9); border-radius:16px; }
  .chart-title,.status,.toolbar,.point-editor { display:flex; align-items:center; gap:14px; flex-wrap:wrap; } .chart-title { justify-content:space-between; }
  .chart-title span,.hint,.status,.draft { font-size:13px; color:var(--secondary-text-color,#63727c); } .toolbar { margin:22px 0; } .draft { flex:1; }
  .badge { border:1px solid var(--divider-color,#d6e2e5); border-radius:20px; padding:5px 10px; color:var(--primary-color,#347f91); font-weight:600; }
  label { display:flex; flex-direction:column; gap:6px; font-size:12px; color:var(--secondary-text-color,#63727c); }
  input,select,button { font:inherit; color:var(--primary-text-color,#18242c); background:var(--card-background-color,#fff); border:1px solid var(--divider-color,#cdd8df); border-radius:8px; padding:9px 12px; min-height:40px; }
  input { width:145px; } button { cursor:pointer; font-size:13px; } button:disabled { opacity:.45; cursor:default; } select { min-width:130px; } .primary { background:var(--primary-color,#347f91); color:var(--text-primary-color,#fff); border-color:transparent; }
  .point-editor { align-items:flex-end; } svg { display:block; width:100%; height:240px; margin:12px 0 6px; overflow:visible; user-select:none; }
  svg text { fill:var(--secondary-text-color,#63727c); font-size:12px; } .grid { stroke:var(--divider-color,#e4e9ed); stroke-width:1; } .now { stroke:var(--primary-text-color,#263843); stroke-width:1.5; stroke-dasharray:4 4; opacity:.55; pointer-events:none; }
  .handle { fill:var(--card-background-color,#fff); stroke:var(--primary-color,#347f91); stroke-width:2; pointer-events:none; } .handle.selected { fill:var(--primary-color,#347f91); } .hit { fill:transparent; cursor:grab; touch-action:none; } :focus-visible { outline:3px solid var(--primary-color,#347f91); outline-offset:3px; } #menu { margin-bottom:8px; padding:3px 10px; font-size:20px; }
  .light { display:flex; align-items:center; justify-content:space-between; gap:15px; padding:14px 0; border-bottom:1px solid var(--divider-color,#e4e9ed); } .light:last-child { border:0; } small { display:block; margin-top:5px; color:var(--secondary-text-color,#63727c); font-size:12px; }
  .error,.notice,.success { padding:12px 16px; border-radius:9px; margin:12px 0; font-size:14px; } .error { background:#b32a2517; color:var(--error-color,#b32a25); } .notice { background:#df9b2418; color:var(--primary-text-color,#583f13); } .success { background:#34855a18; color:var(--primary-text-color,#246542); }
  @media(max-width:650px) { main { padding:20px 12px 40px; } header { align-items:flex-start; flex-direction:column; gap:12px; } h1 { font-size:25px; } section { padding:14px; } .point-editor { gap:10px; } .point-editor label { flex:1; } input { width:100%; min-width:100px; } .toolbar { gap:8px; } .toolbar .draft { flex-basis:100%; order:3; } .light strong { font-size:14px; } }
`;

if (!customElements.get("custom-lighting-panel")) customElements.define("custom-lighting-panel", CustomLightingPanel);
