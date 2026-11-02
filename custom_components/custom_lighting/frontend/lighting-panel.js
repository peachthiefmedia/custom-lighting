/* Custom Lighting combined daily keyframe editor. No external assets required. */
const clone = value => JSON.parse(JSON.stringify(value));
const escapeHtml = value => String(value).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const clock = minute => String(Math.floor(minute / 60)).padStart(2, "0") + ":" + String(minute % 60).padStart(2, "0");
const roundEven = value => value % 1 === 0.5 ? Math.floor(value / 2) * 2 + (Math.floor(value) % 2 ? 2 : 0) : Math.round(value);
const hexOf = rgb => "#" + rgb.map(v => Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, "0")).join("");
const rgbOf = hex => [1, 3, 5].map(index => parseInt(hex.slice(index, index + 2), 16));

export function temperatureToRgb(kelvin) {
  const t = kelvin / 100;
  const red = t <= 66 ? 255 : 329.698727446 * (t - 60) ** -0.1332047592;
  const green = t <= 66 ? 99.4708025861 * Math.log(t) - 161.1195681661 : 288.1221695283 * (t - 60) ** -0.0755148492;
  const blue = t >= 66 ? 255 : t <= 19 ? 0 : 138.5177312231 * Math.log(t - 10) - 305.0447927307;
  return [red, green, blue].map(v => roundEven(Math.max(0, Math.min(255, v))));
}
const pointRgb = point => point.color.type === "temperature" ? temperatureToRgb(point.color.value) : point.color.value;

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

export function evaluateCurve(curve, minute) {
  const points = curve.points, n = points.length;
  minute = ((minute % 1440) + 1440) % 1440;
  let index = points.findIndex(p => p.time > minute) - 1;
  if (index < 0) index = n - 1;
  const a = points[index], b = points[(index + 1) % n];
  const u = ((minute - a.time + 1440) % 1440) / ((b.time - a.time + 1440) % 1440);
  let kelvin = null, rgb;
  if (a.color.type === "temperature" && b.color.type === "temperature") {
    kelvin = roundEven(a.color.value + u * (b.color.value - a.color.value));
    rgb = temperatureToRgb(kelvin);
  } else {
    const start = pointRgb(a), end = pointRgb(b);
    rgb = start.map((v, i) => roundEven(v + u * (end[i] - v)));
  }
  return {
    brightness_pct: interpolate(points.map(p => ({time:p.time, value:p.brightness})), minute, curve.interpolation),
    color_temp_kelvin: kelvin, rgb_color: rgb,
  };
}

export class CustomLightingPanel extends HTMLElement {
  constructor() {
    super(); this.attachShadow({mode:"open"});
    this._profiles = []; this._selected = 0; this._history = [];
    this._busy = false; this._error = ""; this._message = "";
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
    this._beforeUnload = event => { if (this._dirty()) { event.preventDefault(); event.returnValue = ""; } };
    window.addEventListener("beforeunload", this._beforeUnload);
    this._resizeObserver = new ResizeObserver(() => this._draw());
    this._resizeObserver.observe(this);
    this.render();
  }
  disconnectedCallback() {
    clearInterval(this._timer); this._resizeObserver?.disconnect();
    window.removeEventListener("beforeunload", this._beforeUnload); this._drag = null;
  }
  _dirty() { return this._draft && JSON.stringify(this._draft) !== JSON.stringify(this._saved); }
  async _refresh() {
    if (!this._hass || this._loading || this._drag || this._busy) return;
    this._loading = true;
    try {
      const profiles = await this._hass.callWS({type:"custom_lighting/profiles"});
      if (this._drag || this._busy) return;
      this._profiles = profiles; this._loaded = true;
      const profile = profiles.find(p => p.id === this._id) || profiles[0];
      if (profile) {
        if (this._id !== profile.id) {
          if (this._dirty()) { this._error = "Profile not loaded. Your draft is kept until it returns."; this.render(); return; }
          this._loadProfile(profile);
        } else if (profile.revision !== this._revision) {
          if (this._dirty()) this._conflict = true;
          else this._loadProfile(profile);
        }
      }
      this._error = "";
      if (!this.shadowRoot.activeElement?.closest(".point-editor")) this.render();
      else this._draw();
    } catch (error) { this._error = error.message || String(error); this.render(); }
    finally { this._loading = false; }
  }
  _loadProfile(profile) {
    this._id = profile.id; this._saved = clone(profile.curve); this._draft = clone(profile.curve);
    this._revision = profile.revision; this._selected = 0; this._history = [];
    this._conflict = false; this._message = "";
  }
  _remember() {
    this._history.push(clone(this._draft));
    if (this._history.length > 50) this._history.shift();
    this._message = ""; this._error = "";
  }
  _redrawDraft() {
    this._draw();
    this.shadowRoot.querySelector(".draft").textContent = this._dirty() ? "Unsaved changes" : "Saved schedule";
    this.shadowRoot.querySelector("#save").disabled = !this._dirty() || this._busy || this._conflict;
    this.shadowRoot.querySelector("#undo").disabled = !this._history.length || this._busy;
    this.shadowRoot.querySelector(".error")?.remove();
    this.shadowRoot.querySelector(".success")?.remove();
  }
  _now() {
    const profile = this._profiles.find(p => p.id === this._id);
    const parts = new Intl.DateTimeFormat("en-GB", {
      timeZone:profile?.timezone || this._hass?.config?.time_zone || "UTC",
      hour:"2-digit", minute:"2-digit", hourCycle:"h23",
    }).formatToParts(new Date());
    return Number(parts.find(p => p.type === "hour").value) * 60 + Number(parts.find(p => p.type === "minute").value);
  }
  _coordinate(event) {
    const box = this.shadowRoot.querySelector("svg").getBoundingClientRect();
    const x = Math.max(0, Math.min(1, (event.clientX - box.left - 55) / (box.width - 75)));
    const y = Math.max(0, Math.min(1, (event.clientY - box.top - 20) / 170));
    return {time:Math.min(1425, Math.round(x * 1440 / 15) * 15), brightness:Math.max(1, Math.min(100, Math.round(100 - y * 99)))};
  }
  _movePoint(point, time, brightness) {
    if (this._draft.points.some(p => p !== point && p.time === time)) return false;
    point.time = time; point.brightness = brightness;
    this._draft.points.sort((a,b) => a.time - b.time);
    this._selected = this._draft.points.indexOf(point); return true;
  }
  _add(coordinate) {
    const points = this._draft.points;
    if (points.length >= 96) { this._error = "Use at most 96 points."; this.render(); return; }
    let time = Math.min(1425, Math.round((coordinate?.time ?? this._now()) / 15) * 15);
    for (let checked=0; checked<96 && points.some(p => p.time === time); checked++) time = (time + 15) % 1440;
    if (points.some(p => p.time === time)) { this._error = "Every 15-minute slot already has a point."; this.render(); return; }
    const target = evaluateCurve(this._draft, time);
    this._remember();
    const point = {time, brightness:coordinate?.brightness ?? Math.round(target.brightness_pct),
      color:target.color_temp_kelvin !== null ? {type:"temperature", value:target.color_temp_kelvin} : {type:"rgb", value:target.rgb_color}};
    points.push(point); points.sort((a,b) => a.time - b.time); this._selected = points.indexOf(point); this.render();
  }
  _remove() {
    if (this._draft.points.length <= 2) return;
    this._remember(); this._draft.points.splice(this._selected,1);
    this._selected = Math.min(this._selected, this._draft.points.length-1); this.render();
  }
  _draw() {
    const svg = this.shadowRoot.querySelector("svg");
    if (!svg || !this._draft) return;
    const width = svg.getBoundingClientRect().width || 900;
    svg.setAttribute("viewBox", "0 0 " + width + " 260");
    const x = t => 55 + t / 1440 * (width - 75), y = b => 20 + (100-b)/99*170;
    let markup = "";
    for (let time=0; time<=1440; time+=15) {
      markup += '<line class="grid" x1="' + x(time) + '" x2="' + x(time) + '" y1="223" y2="' + (time%60===0?231:227) + '"/>';
    }
    for (let hour=0; hour<=24; hour += width < 500 ? 6 : 3) {
      markup += '<line class="grid" x1="' + x(hour*60) + '" x2="' + x(hour*60) + '" y1="20" y2="190"/>';
      markup += '<text x="' + x(hour*60) + '" y="245" text-anchor="middle">' + String(hour).padStart(2,"0") + ':00</text>';
    }
    for (const value of [1,25,50,75,100]) {
      markup += '<line class="grid" x1="55" x2="' + (width-20) + '" y1="' + y(value) + '" y2="' + y(value) + '"/>';
      markup += '<text x="45" y="' + (y(value)+4) + '" text-anchor="end">' + value + '</text>';
    }
    let path = "";
    for (let time=0; time<=1440; time+=5) {
      const target = evaluateCurve(this._draft, time);
      path += (time ? " L" : "M") + x(time).toFixed(2) + "," + y(target.brightness_pct).toFixed(2);
    }
    markup += '<path d="' + path + '" fill="none" stroke="#e6a13a" stroke-width="3"/>';
    let stops = "";
    for (let time=0; time<=1440; time+=6) {
      const color = hexOf(evaluateCurve(this._draft,time).rgb_color);
      stops += '<stop offset="' + (time/1440*100) + '%" stop-color="' + color + '"/>';
    }
    markup += '<defs><linearGradient id="schedule-colours">' + stops + '</linearGradient></defs>'
      + '<rect x="55" y="205" width="' + (width-75) + '" height="15" fill="url(#schedule-colours)"/>';
    markup += '<line class="now" x1="' + x(this._now()) + '" x2="' + x(this._now()) + '" y1="15" y2="223"/>';
    markup += this._draft.points.map((p,i) =>
      '<circle cx="' + x(p.time) + '" cy="' + y(p.brightness) + '" r="' + (i===this._selected?8:6)
      + '" class="handle" style="fill:' + hexOf(pointRgb(p)) + '"/><circle class="hit" data-index="' + i
      + '" cx="' + x(p.time) + '" cy="' + y(p.brightness) + '" r="16" tabindex="0" role="slider" aria-label="Point '
      + (i+1) + ' at ' + clock(p.time) + '" aria-valuemin="1" aria-valuemax="100" aria-valuenow="' + p.brightness
      + '"><title>' + clock(p.time) + " · " + p.brightness + "% · "
      + (p.color.type==="temperature" ? p.color.value + " K" : hexOf(p.color.value)) + '</title></circle>').join("");
    svg.innerHTML = markup;
  }
  _bindChart() {
    const svg = this.shadowRoot.querySelector("svg");
    svg.addEventListener("pointerdown", event => {
      if (this._busy || event.button !== 0) return;
      const handle = event.target.closest("[data-index]"); if (!handle) return;
      event.preventDefault(); this._selected = Number(handle.dataset.index); this._remember();
      this._drag = this._draft.points[this._selected]; svg.setPointerCapture(event.pointerId); this._draw();
    });
    svg.addEventListener("pointermove", event => {
      if (!this._drag) return;
      const coordinate = this._coordinate(event);
      this._movePoint(this._drag, coordinate.time, coordinate.brightness); this._draw();
      this.shadowRoot.querySelector("#point-time").value = clock(this._drag.time);
      this.shadowRoot.querySelector("#point-brightness").value = this._drag.brightness;
    });
    const end = () => { if (this._drag) { this._drag = null; this.render(); } };
    svg.addEventListener("pointerup",end); svg.addEventListener("pointercancel",end); svg.addEventListener("lostpointercapture",end);
    svg.addEventListener("dblclick", event => { if (!this._busy && !event.target.closest("[data-index]")) this._add(this._coordinate(event)); });
    svg.addEventListener("keydown", event => {
      const handle = event.target.closest("[data-index]"); if (!handle || this._busy) return;
      this._selected = Number(handle.dataset.index); const point = this._draft.points[this._selected];
      if (["Delete","Backspace"].includes(event.key)) { event.preventDefault(); this._remove(); return; }
      if (!["ArrowLeft","ArrowRight","ArrowUp","ArrowDown"].includes(event.key)) return;
      event.preventDefault(); this._remember();
      const time = ["ArrowLeft","ArrowRight"].includes(event.key)
        ? Math.max(0, Math.min(1425, (event.key==="ArrowLeft"?Math.ceil(point.time/15)-1:Math.floor(point.time/15)+1)*15)) : point.time;
      const brightness = Math.max(1, Math.min(100, point.brightness + (event.key==="ArrowUp"?1:event.key==="ArrowDown"?-1:0)*(event.shiftKey?10:1)));
      this._movePoint(point,time,brightness); this.render();
      this.shadowRoot.querySelector('[data-index="' + this._selected + '"]')?.focus();
    });
  }
  async _save() {
    this._busy = true; this._error = ""; this.render();
    try {
      const profile = await this._hass.callWS({type:"custom_lighting/save_curve", entry_id:this._id, revision:this._revision, curve:this._draft});
      this._profiles = this._profiles.map(p => p.id===profile.id ? profile : p); this._loadProfile(profile);
      this._message = "Saved. Scheduled lights now follow these points.";
    } catch(error) { this._error = error.message || String(error); }
    finally { this._busy = false; this.render(); }
  }
  async _mode(light,mode) {
    this._busy = true; this._error = ""; this.render();
    try {
      const profile = await this._hass.callWS({type:"custom_lighting/set_mode", entry_id:this._id, light, mode});
      this._profiles = this._profiles.map(p => p.id===profile.id ? profile : p);
    } catch(error) { this._error = error.message || String(error); }
    finally { this._busy = false; this.render(); }
  }
  async _resumeAll() {
    this._busy = true; this._error = ""; this.render();
    try {
      await this._hass.callService("custom_lighting","resume_schedule",{});
      this._profiles = await this._hass.callWS({type:"custom_lighting/profiles"});
      this._message = "All lights returned to Schedule mode. Paused profiles remain paused.";
    } catch(error) { this._error = error.message || String(error); }
    finally { this._busy = false; this.render(); }
  }
  render() {
    const root = this.shadowRoot, profile = this._profiles.find(p => p.id===this._id);
    const disabled = this._busy ? "disabled" : "";
    const point = this._draft?.points[this._selected];
    const colorFields = point?.color.type === "rgb"
      ? '<label>RGB colour<input id="point-rgb" type="color" value="' + hexOf(point.color.value) + '" ' + disabled
        + '></label><label>Hex value<input id="point-hex" value="' + hexOf(point.color.value) + '" pattern="#[0-9a-fA-F]{6}" maxlength="7" ' + disabled + '></label>'
      : '<label>Temperature (K)<input id="point-kelvin" type="number" min="1000" max="10000" step="50" value="' + (point?.color.value || 3000) + '" ' + disabled + '></label>';
    root.innerHTML = '<style>' + STYLES + '</style><main><button id="menu" aria-label="Open Home Assistant sidebar">☰</button>'
      + '<header><div><p class="eyebrow">CUSTOM LIGHTING</p><h1>Daily lighting schedule</h1><p>One timeline for brightness and colour. Select a point to edit it.</p></div>'
      + '<label>Profile<select id="profile" ' + disabled + '>' + this._profiles.map(p => '<option value="' + escapeHtml(p.id) + '" '
        + (p.id===this._id?"selected":"") + '>' + escapeHtml(p.name) + '</option>').join("") + '</select></label></header>'
      + '<button id="resume-all" ' + (!this._profiles.length || this._busy?"disabled":"") + '>Resume all schedules</button>'
      + (this._error ? '<div class="error" role="alert">' + escapeHtml(this._error) + '</div>' : "")
      + (this._message ? '<div class="success" role="status">' + escapeHtml(this._message) + '</div>' : "")
      + (profile && point ? '<div class="status"><span class="badge">' + (profile.enabled?"Enabled":"Profile paused") + '</span><span>Now ' + clock(this._now())
        + ' · ' + Math.round(profile.targets.brightness_pct) + '% · '
        + (profile.targets.color_temp_kelvin !== null ? profile.targets.color_temp_kelvin + ' K' : 'RGB ' + hexOf(profile.targets.rgb_color))
        + '</span><span>' + escapeHtml(profile.timezone) + '</span></div>'
        + (!profile.enabled ? '<p class="notice">This profile is paused. Enable its main switch in Home Assistant to apply the schedule.</p>' : "")
        + (this._conflict ? '<p class="notice">The saved schedule changed elsewhere. Your draft is kept. Revert to load the latest version.</p>' : "")
        + '<div class="toolbar"><label>Brightness shape<select id="interpolation" ' + disabled + '><option value="smooth" '
        + (this._draft.interpolation==="smooth"?"selected":"") + '>Smooth</option><option value="linear" '
        + (this._draft.interpolation==="linear"?"selected":"") + '>Linear ramps</option></select></label><span class="draft">'
        + (this._dirty()?"Unsaved changes":"Saved schedule") + '</span><button id="undo" ' + (!this._history.length || this._busy?"disabled":"") + '>Undo</button>'
        + '<button id="revert" ' + disabled + '>Revert</button><button id="save" class="primary" ' + (!this._dirty() || this._busy || this._conflict?"disabled":"") + '>Save schedule</button></div>'
        + '<section><div class="chart-title"><h2>Brightness and colour</h2><span>' + this._draft.points.length + ' points · brightness %</span></div>'
        + '<svg viewBox="0 0 1000 260" role="group" aria-label="Daily brightness and colour schedule"></svg>'
        + '<div class="point-editor"><label>Time<input id="point-time" type="time" step="900" max="23:45" value="' + clock(point.time) + '" ' + disabled + '></label>'
        + '<label>Brightness (%)<input id="point-brightness" type="number" min="1" max="100" step="1" value="' + point.brightness + '" ' + disabled + '></label>'
        + '<label>Colour input<select id="color-type" ' + disabled + '><option value="temperature" ' + (point.color.type==="temperature"?"selected":"") + '>Temperature</option>'
        + '<option value="rgb" ' + (point.color.type==="rgb"?"selected":"") + '>RGB colour</option></select></label>' + colorFields
        + '<button id="add" ' + disabled + '>Add point</button><button id="remove" ' + (this._draft.points.length<=2 || this._busy?"disabled":"") + '>Remove selected</button></div>'
        + '<p class="hint">The strip previews colour. Temperature sections interpolate in Kelvin; sections with RGB blend colours. RGB requires colour-capable bulbs; white-only bulbs keep following brightness.</p></section>'
        + '<p class="hint">Times snap to 15 minutes. Double-click to add. Arrow keys adjust a selected point; Delete removes it. Editing is a draft until saved.</p>'
        + '<section><h2>Light control</h2><p>Manual changes pause this light. Select Schedule to resume, or use Resume all schedules.</p><div class="lights">'
        + profile.lights.map(light => '<div class="light"><div><strong>' + escapeHtml(light.name) + '</strong><small>' + escapeHtml(light.entity_id) + ' · '
          + escapeHtml(light.state) + '</small></div><label>Mode<select data-light="' + escapeHtml(light.entity_id) + '" ' + disabled + '><option '
          + (light.mode==="Schedule"?"selected":"") + '>Schedule</option><option ' + (light.mode==="Manual"?"selected":"") + '>Manual</option></select></label></div>').join("")
        + '</div><p class="hint">Modes survive off/on cycles and restarts. Off lights stay off.</p></section>'
        : '<section><h2>' + (this._loaded?"No profiles loaded":"Loading lighting profiles…") + '</h2><p>Add Custom Lighting in Settings → Devices &amp; services.</p></section>')
      + '</main>';
    root.querySelector("#menu").addEventListener("click", () => this.dispatchEvent(new CustomEvent("hass-toggle-menu",{bubbles:true,composed:true})));
    root.querySelector("#resume-all").addEventListener("click", () => this._resumeAll());
    root.querySelector("#profile").addEventListener("change", event => {
      if (this._dirty() && !window.confirm("Discard unsaved changes and switch profiles?")) { this.render(); return; }
      this._loadProfile(this._profiles.find(p => p.id===event.target.value)); this.render();
    });
    if (!profile || !point) return;
    root.querySelector("#interpolation").addEventListener("change", event => {
      this._remember(); this._draft.interpolation = event.target.value; this.render();
    });
    root.querySelector("#save").addEventListener("click", () => this._save());
    root.querySelector("#undo").addEventListener("click", () => {
      if (this._history.length) { this._draft = this._history.pop(); this._selected = 0; this.render(); }
    });
    root.querySelector("#revert").addEventListener("click", () => {
      if (this._dirty() && !window.confirm("Discard unsaved changes?")) return;
      this._loadProfile(profile); this.render();
    });
    root.querySelector("#add").addEventListener("click", () => this._add());
    root.querySelector("#remove").addEventListener("click", () => this._remove());
    root.querySelector("#color-type").addEventListener("change", event => {
      this._remember();
      point.color = event.target.value==="rgb" ? {type:"rgb",value:pointRgb(point)} : {type:"temperature",value:3000};
      this.render();
    });
    const updatePoint = event => {
      const timeText = root.querySelector("#point-time").value, input = root.querySelector("#point-brightness");
      const [hours,minutes] = timeText.split(":").map(Number), time=hours*60+minutes, brightness=Number(input.value);
      if (!/^\d{2}:\d{2}$/.test(timeText) || !input.value || !Number.isFinite(brightness) || brightness<1 || brightness>100 || time<0 || time>=1440) {
        if (event.type==="input") { root.querySelector("#save").disabled=true; return; }
        this._error = "Enter a valid time and brightness from 1 to 100%."; this.render(); return;
      }
      if (point.time===time && point.brightness===brightness) { this._redrawDraft(); return; }
      this._remember();
      const snappedTime = event.target.id==="point-time" ? Math.min(1425, Math.round(time/15)*15) : time;
      if (!this._movePoint(point,snappedTime,brightness)) this._error = "Two points cannot have the same time.";
      root.querySelector("#point-time").value = clock(point.time);
      if (this._error) this.render(); else this._redrawDraft();
    };
    root.querySelector("#point-time").addEventListener("change",updatePoint);
    root.querySelector("#point-time").addEventListener("input",updatePoint);
    root.querySelector("#point-brightness").addEventListener("change",updatePoint);
    root.querySelector("#point-brightness").addEventListener("input",updatePoint);
    const updateKelvin = event => {
      const value=Number(event.target.value);
      if (!event.target.value || !Number.isFinite(value) || value<1000 || value>10000) {
        if (event.type==="input") { root.querySelector("#save").disabled=true; return; }
        this._error="Temperature must be 1000–10000 K."; this.render(); return;
      }
      if (point.color.value===Math.round(value)) { this._redrawDraft(); return; }
      this._remember(); point.color.value=Math.round(value); event.target.value=point.color.value; this._redrawDraft();
    };
    root.querySelector("#point-kelvin")?.addEventListener("change",updateKelvin);
    root.querySelector("#point-kelvin")?.addEventListener("input",updateKelvin);
    const updateRgb = (value,event) => {
      if (!/^#[0-9a-fA-F]{6}$/.test(value)) {
        if (event.type==="input") { root.querySelector("#save").disabled=true; return; }
        this._error="Enter a six-digit hex colour, such as #ff8800."; this.render(); return;
      }
      if (hexOf(point.color.value)===value.toLowerCase()) { this._redrawDraft(); return; }
      this._remember(); point.color.value=rgbOf(value);
      root.querySelector("#point-rgb").value=hexOf(point.color.value);
      root.querySelector("#point-hex").value=hexOf(point.color.value);
      this._redrawDraft();
    };
    root.querySelector("#point-rgb")?.addEventListener("change",event => updateRgb(event.target.value,event));
    root.querySelector("#point-hex")?.addEventListener("change",event => updateRgb(event.target.value,event));
    root.querySelector("#point-hex")?.addEventListener("input",event => updateRgb(event.target.value,event));
    root.querySelectorAll("[data-light]").forEach(select => select.addEventListener("change",()=>this._mode(select.dataset.light,select.value)));
    this._bindChart(); this._draw();
  }
}

const STYLES = "\n  :host { display:block; height:100%; overflow:auto; background:var(--primary-background-color,#f4f6f8); color:var(--primary-text-color,#18242c); font-family:var(--paper-font-body1_-_font-family,system-ui,sans-serif); }\n  * { box-sizing:border-box; } main { max-width:1120px; padding:32px 28px 60px; margin:auto; }\n  header { display:flex; justify-content:space-between; align-items:center; gap:24px; margin-bottom:22px; }\n  h1 { font-size:30px; letter-spacing:-.7px; margin:5px 0 8px; } h2 { font-size:18px; margin:0; font-weight:600; }\n  p { line-height:1.5; color:var(--secondary-text-color,#63727c); margin:8px 0; } .eyebrow { font-size:11px; font-weight:700; letter-spacing:2px; color:var(--primary-color,#347f91); }\n  section { padding:22px; margin:18px 0; background:var(--card-background-color,#fff); border:1px solid var(--divider-color,#dde4e9); border-radius:16px; }\n  .chart-title,.status,.toolbar,.point-editor { display:flex; align-items:center; gap:14px; flex-wrap:wrap; } .chart-title { justify-content:space-between; }\n  .chart-title span,.hint,.status,.draft { font-size:13px; color:var(--secondary-text-color,#63727c); } .toolbar { margin:22px 0; } .draft { flex:1; }\n  .badge { border:1px solid var(--divider-color,#d6e2e5); border-radius:20px; padding:5px 10px; color:var(--primary-color,#347f91); font-weight:600; }\n  label { display:flex; flex-direction:column; gap:6px; font-size:12px; color:var(--secondary-text-color,#63727c); }\n  input,select,button { font:inherit; color:var(--primary-text-color,#18242c); background:var(--card-background-color,#fff); border:1px solid var(--divider-color,#cdd8df); border-radius:8px; padding:9px 12px; min-height:40px; }\n  input { width:145px; } button { cursor:pointer; font-size:13px; } button:disabled { opacity:.45; cursor:default; } select { min-width:130px; } .primary { background:var(--primary-color,#347f91); color:var(--text-primary-color,#fff); border-color:transparent; }\n  .point-editor { align-items:flex-end; } svg { display:block; width:100%; height:240px; margin:12px 0 6px; overflow:visible; user-select:none; }\n  svg text { fill:var(--secondary-text-color,#63727c); font-size:12px; } .grid { stroke:var(--divider-color,#e4e9ed); stroke-width:1; } .now { stroke:var(--primary-text-color,#263843); stroke-width:1.5; stroke-dasharray:4 4; opacity:.55; pointer-events:none; }\n  .handle { fill:var(--card-background-color,#fff); stroke:var(--primary-color,#347f91); stroke-width:2; pointer-events:none; } .handle.selected { fill:var(--primary-color,#347f91); } .hit { fill:transparent; cursor:grab; touch-action:none; } :focus-visible { outline:3px solid var(--primary-color,#347f91); outline-offset:3px; } #menu { margin-bottom:8px; padding:3px 10px; font-size:20px; }\n  .light { display:flex; align-items:center; justify-content:space-between; gap:15px; padding:14px 0; border-bottom:1px solid var(--divider-color,#e4e9ed); } .light:last-child { border:0; } small { display:block; margin-top:5px; color:var(--secondary-text-color,#63727c); font-size:12px; }\n  .error,.notice,.success { padding:12px 16px; border-radius:9px; margin:12px 0; font-size:14px; } .error { background:#b32a2517; color:var(--error-color,#b32a25); } .notice { background:#df9b2418; color:var(--primary-text-color,#583f13); } .success { background:#34855a18; color:var(--primary-text-color,#246542); }\n  @media(max-width:650px) { main { padding:20px 12px 40px; } header { align-items:flex-start; flex-direction:column; gap:12px; } h1 { font-size:25px; } section { padding:14px; } .point-editor { gap:10px; } .point-editor label { flex:1; } input { width:100%; min-width:100px; } .toolbar { gap:8px; } .toolbar .draft { flex-basis:100%; order:3; } .light strong { font-size:14px; } }\n\nsvg { height:260px; }\n.status { margin-top:18px; }\n#point-rgb { padding:3px; width:100px; height:40px; cursor:pointer; }\n#point-hex { width:110px; font-family:monospace; }\n.point-editor { row-gap:14px; }\n@media(max-width:650px) { .point-editor label { flex:1 1 130px; } #point-hex,#point-rgb { width:100%; } }\n";

if (!customElements.get("custom-lighting-panel")) customElements.define("custom-lighting-panel", CustomLightingPanel);
