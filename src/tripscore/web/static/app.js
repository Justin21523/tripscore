function toTaipeiIso(datetimeLocalValue) {
  if (!datetimeLocalValue) return null;
  // datetime-local yields "YYYY-MM-DDTHH:MM" without timezone.
  // MVP convention: treat UI values as Asia/Taipei (+08:00).
  return `${datetimeLocalValue}:00+08:00`;
}

function fromTaipeiIso(isoValue) {
  if (!isoValue) return "";
  const s = String(isoValue);
  return s.replace(":00+08:00", "").replace(":00+00:00", "").replace("Z", "");
}

function el(id) {
  return document.getElementById(id);
}

function numberValue(id) {
  const v = el(id).value;
  return v === "" ? null : Number(v);
}

function setNumberValue(id, value) {
  const node = el(id);
  node.value = value === null || value === undefined ? "" : String(value);
}

function setTextValue(id, value) {
  el(id).value = value || "";
}

function parseTagList(value) {
  if (!value) return [];
  return value
    .split(",")
    .map((t) => t.trim().toLowerCase())
    .filter((t) => t.length > 0);
}

function pad2(n) {
  return String(n).padStart(2, "0");
}

function formatLocalDatetime(date) {
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}T${pad2(
    date.getHours()
  )}:${pad2(date.getMinutes())}`;
}

function isTypingTarget(target) {
  if (!target) return false;
  const tag = String(target.tagName || "").toLowerCase();
  return tag === "input" || tag === "textarea" || tag === "select";
}

function clamp(n, min, max) {
  return Math.max(min, Math.min(max, n));
}

function pruneEmpty(obj) {
  if (!obj || typeof obj !== "object") return obj;
  if (Array.isArray(obj)) return obj;
  const out = {};
  Object.keys(obj).forEach((k) => {
    const v = pruneEmpty(obj[k]);
    const emptyObj = v && typeof v === "object" && !Array.isArray(v) && Object.keys(v).length === 0;
    if (v !== null && v !== undefined && !emptyObj) out[k] = v;
  });
  return out;
}

function deepGet(obj, path) {
  let cur = obj;
  for (const key of path) {
    if (!cur || typeof cur !== "object" || !(key in cur)) return undefined;
    cur = cur[key];
  }
  return cur;
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch (_) {
    try {
      window.prompt("Copy to clipboard:", text);
      return true;
    } catch (e) {
      return false;
    }
  }
}

const STORAGE = {
  lastQueryV1: "tripscore.last_query.v1",
  lastQueryV2: "tripscore.last_query.v2",
  uiState: "tripscore.ui_state.v1",
  customPresets: "tripscore.custom_presets.v1",
};

const state = {
  applyingPreset: false,
  autoRun: false,
  keyboardControls: true,
  pickOrigin: false,
  overridesEnabled: false,
  moveStepM: 50,
  headingDeg: 0,
  settings: null,
  serverPresets: {},
  customPresets: {},
  lastResponse: null,
  resultsById: {},
  resultOrder: [],
  selectedId: null,
  map: null,
  originMarker: null,
  headingLine: null,
  destMarkers: {},
};

function setStatus(message) {
  el("status").textContent = message;
}

async function fetchJson(url) {
  const resp = await fetch(url);
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return await resp.json();
}

function originIcon() {
  if (!window.L) return null;
  return L.divIcon({
    className: "origin-icon",
    html: `<div class="origin-icon-inner">O</div>`,
    iconSize: [28, 28],
    iconAnchor: [14, 14],
  });
}

function destIcon(rank, selected) {
  if (!window.L) return null;
  const cls = selected ? "dest-icon selected" : "dest-icon";
  return L.divIcon({
    className: cls,
    html: `<div class="dest-icon-inner">${rank}</div>`,
    iconSize: [30, 30],
    iconAnchor: [15, 15],
    popupAnchor: [0, -14],
  });
}

function ensureMap(originLat, originLon) {
  if (!window.L) return null;
  if (state.map) return state.map;

  const m = L.map("map", { zoomControl: true }).setView([originLat, originLon], 12);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: "&copy; OpenStreetMap contributors",
  }).addTo(m);

  m.on("click", (evt) => {
    if (!state.pickOrigin) return;
    updateOrigin(evt.latlng.lat, evt.latlng.lng, { center: false, source: "map_click" });
  });

  state.map = m;
  return m;
}

function clearMarkers() {
  if (!state.map) return;
  Object.values(state.destMarkers).forEach((m) => m.remove());
  state.destMarkers = {};
}

function updateHeadingLine() {
  if (!state.map) return;
  const originLat = numberValue("origin-lat");
  const originLon = numberValue("origin-lon");
  if (originLat === null || originLon === null) return;

  const heading = clamp(Number(state.headingDeg) || 0, 0, 359);
  const distM = 1200;
  const rad = (heading * Math.PI) / 180;
  const latRad = (originLat * Math.PI) / 180;
  const dLat = (distM / 111000) * Math.cos(rad);
  const dLon = (distM / (111000 * Math.max(Math.cos(latRad), 0.2))) * Math.sin(rad);
  const to = [originLat + dLat, originLon + dLon];

  const points = [
    [originLat, originLon],
    to,
  ];
  if (!state.headingLine) {
    state.headingLine = L.polyline(points, { color: "#7ee0ff", weight: 3, dashArray: "6 8" }).addTo(state.map);
  } else {
    state.headingLine.setLatLngs(points);
  }
}

function updateOrigin(lat, lon, { center, source } = { center: false, source: "manual" }) {
  setNumberValue("origin-lat", lat);
  setNumberValue("origin-lon", lon);

  const m = ensureMap(lat, lon);
  if (m && center) m.setView([lat, lon], Math.max(m.getZoom(), 12));

  if (m) {
    if (!state.originMarker) {
      const marker = L.marker([lat, lon], { draggable: true, icon: originIcon() }).addTo(m);
      marker.on("dragend", () => {
        const p = marker.getLatLng();
        updateOrigin(p.lat, p.lng, { center: false, source: "drag" });
        if (state.autoRun) scheduleAutoRun("origin_drag");
      });
      state.originMarker = marker;
    } else {
      state.originMarker.setLatLng([lat, lon]);
    }
  }

  updateHeadingLine();
  if (source === "keyboard" || source === "dpad" || source === "map_click") {
    if (state.autoRun) scheduleAutoRun("origin_move");
  }
}

function selectResult(id) {
  if (!id || !(id in state.resultsById)) return;
  state.selectedId = id;
  const item = state.resultsById[id];

  const inspector = el("inspector");
  inspector.innerHTML = "";

  const dest = item.destination;
  const breakdown = item.breakdown;

  const title = document.createElement("div");
  title.className = "title";
  title.textContent = `${dest.name} — total ${Number(breakdown.total_score).toFixed(3)}`;

  const meta = document.createElement("div");
  meta.className = "meta";
  meta.textContent = `${dest.city || ""} ${dest.district || ""}`.trim();

  const tags = document.createElement("div");
  tags.className = "tags";
  (dest.tags || []).forEach((t) => {
    const chip = document.createElement("span");
    chip.className = "tag";
    chip.textContent = t;
    tags.appendChild(chip);
  });

  const link = document.createElement("div");
  link.className = "meta";
  if (dest.url) {
    const a = document.createElement("a");
    a.href = dest.url;
    a.target = "_blank";
    a.rel = "noreferrer";
    a.textContent = "Open link";
    link.appendChild(a);
  }

  inspector.appendChild(title);
  inspector.appendChild(meta);
  if ((dest.tags || []).length > 0) inspector.appendChild(tags);
  if (dest.url) inspector.appendChild(link);

  breakdown.components.forEach((c) => {
    const d = document.createElement("details");
    d.open = c.name === "accessibility";
    const s = document.createElement("summary");
    s.textContent = `${c.name}: score ${Number(c.score).toFixed(3)} × w ${Number(c.weight).toFixed(
      2
    )} → ${Number(c.contribution).toFixed(3)}`;
    d.appendChild(s);

    const body = document.createElement("div");
    body.className = "section-body";

    if (c.reasons && c.reasons.length) {
      const ul = document.createElement("ul");
      ul.className = "reasons";
      c.reasons.forEach((r) => {
        const li = document.createElement("li");
        li.textContent = r;
        ul.appendChild(li);
      });
      body.appendChild(ul);
    }

    const pre = document.createElement("pre");
    pre.textContent = JSON.stringify(c.details || {}, null, 2);
    body.appendChild(pre);
    d.appendChild(body);
    inspector.appendChild(d);
  });

  // Highlight list selection
  document.querySelectorAll("#results li").forEach((node) => node.classList.remove("selected"));
  const selectedNode = document.querySelector(`#results li[data-id="${CSS.escape(id)}"]`);
  if (selectedNode) selectedNode.classList.add("selected");

  // Highlight map marker
  Object.entries(state.destMarkers).forEach(([destId, marker]) => {
    const rank = marker.options.__rank || 0;
    marker.setIcon(destIcon(rank, destId === id));
  });

  if (state.map && state.destMarkers[id]) {
    state.destMarkers[id].openPopup();
  }
}

function renderResults(payload) {
  state.lastResponse = payload;
  state.resultsById = {};
  state.resultOrder = [];
  state.selectedId = null;

  const resultsEl = el("results");
  resultsEl.innerHTML = "";

  payload.results.forEach((item, idx) => {
    const li = document.createElement("li");
    li.dataset.id = item.destination.id;

    const dest = item.destination;
    const breakdown = item.breakdown;

    const title = document.createElement("div");
    title.className = "result-title";
    title.textContent = `${idx + 1}. ${dest.name} — total ${Number(breakdown.total_score).toFixed(3)}`;

    const meta = document.createElement("div");
    meta.className = "result-meta";
    meta.textContent = `${dest.city || ""} ${dest.district || ""}`.trim();

    const comps = document.createElement("div");
    comps.className = "components";
    breakdown.components.forEach((c) => {
      const chip = document.createElement("div");
      chip.className = "chip";
      chip.textContent = `${c.name}: ${Number(c.contribution).toFixed(3)}`;
      comps.appendChild(chip);
    });

    li.appendChild(title);
    li.appendChild(meta);
    li.appendChild(comps);

    li.addEventListener("click", () => selectResult(dest.id));

    resultsEl.appendChild(li);
    state.resultsById[dest.id] = item;
    state.resultOrder.push(dest.id);
  });

  if (payload.results.length > 0) {
    selectResult(payload.results[0].destination.id);
  }
}

function renderMap(payload) {
  const originLat = numberValue("origin-lat");
  const originLon = numberValue("origin-lon");
  if (originLat === null || originLon === null) return;

  const m = ensureMap(originLat, originLon);
  if (!m) return;

  clearMarkers();
  updateOrigin(originLat, originLon, { center: false, source: "manual" });

  payload.results.forEach((item, idx) => {
    const d = item.destination;
    const marker = L.marker([d.location.lat, d.location.lon], {
      icon: destIcon(idx + 1, false),
      __rank: idx + 1,
    }).addTo(m);
    marker.bindPopup(`${idx + 1}. ${d.name}`);
    marker.on("click", () => selectResult(d.id));
    state.destMarkers[d.id] = marker;
  });
}

function buildAdvancedOverrides() {
  if (!state.overridesEnabled) return null;
  const overrides = {
    ingestion: {
      tdx: {
        accessibility: {
          radius_m: numberValue("acc-bus-radius"),
          count_cap: numberValue("acc-bus-cap"),
          origin_distance_cap_m: numberValue("acc-origin-cap"),
          local_transit_signal_weights: {
            bus: numberValue("acc-w-bus"),
            metro: numberValue("acc-w-metro"),
            bike: numberValue("acc-w-bike"),
          },
          blend_weights: {
            local_transit: numberValue("acc-w-local"),
            origin_proximity: numberValue("acc-w-origin"),
          },
        },
      },
      weather: {
        comfort_temperature_c: {
          min: numberValue("wx-comfort-min"),
          max: numberValue("wx-comfort-max"),
        },
        temperature_penalty_scale_c: numberValue("wx-temp-scale"),
        score_weights: {
          rain: numberValue("wx-w-rain"),
          temperature: numberValue("wx-w-temp"),
        },
      },
    },
    features: {
      weather: {
        rain_importance_multiplier: {
          indoor: numberValue("wx-mult-indoor"),
          outdoor: numberValue("wx-mult-outdoor"),
        },
      },
      context: {
        crowd: {
          weekend_multiplier: numberValue("ctx-weekend-mult"),
          parking_risk_weight: numberValue("ctx-parking-weight"),
        },
        family: {
          tag_bonus: numberValue("ctx-family-bonus"),
        },
      },
      parking: {
        radius_m: numberValue("park-radius"),
        lot_cap: numberValue("park-lot-cap"),
        available_spaces_cap: numberValue("park-spaces-cap"),
      },
    },
  };

  return pruneEmpty(overrides);
}

function parseExpertOverrides() {
  const raw = String(el("overrides-json").value || "").trim();
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      throw new Error("Expected a JSON object.");
    }
    return parsed;
  } catch (e) {
    throw new Error(`Invalid settings_overrides JSON: ${e.message}`);
  }
}

function buildPreferencesPayload() {
  const originLat = numberValue("origin-lat");
  const originLon = numberValue("origin-lon");
  const startIso = toTaipeiIso(el("start").value);
  const endIso = toTaipeiIso(el("end").value);
  const presetSelection = el("preset").value || "";
  const presetName = presetSelection && state.serverPresets[presetSelection] ? presetSelection : null;

  const payload = {
    origin: { lat: originLat, lon: originLon },
    time_window: { start: startIso, end: endIso },
    preset: presetName,
    max_results: numberValue("top-n"),
    weather_rain_importance: numberValue("avoid-rain"),
    avoid_crowds_importance: numberValue("avoid-crowds"),
    family_friendly_importance: numberValue("family-importance"),
    component_weights: {
      accessibility: numberValue("w-accessibility"),
      weather: numberValue("w-weather"),
      preference: numberValue("w-preference"),
      context: numberValue("w-context"),
    },
    tag_weights: {},
    required_tags: parseTagList(el("required-tags").value),
    excluded_tags: parseTagList(el("excluded-tags").value),
  };

  [
    ["indoor", "tag-indoor"],
    ["outdoor", "tag-outdoor"],
    ["culture", "tag-culture"],
    ["food", "tag-food"],
    ["family_friendly", "tag-family"],
    ["crowd_low", "tag-crowd"],
  ].forEach(([tag, id]) => {
    const v = numberValue(id);
    if (v !== null) payload.tag_weights[tag] = v;
  });

  const expert = parseExpertOverrides();
  const advanced = buildAdvancedOverrides();
  payload.settings_overrides = expert || advanced;

  return payload;
}

function saveLastQuery(payload) {
  try {
    const saved = { ...payload, preset_selection: el("preset").value || "" };
    localStorage.setItem(STORAGE.lastQueryV2, JSON.stringify(saved));
  } catch (_) {
    // Ignore
  }
}

function loadLastQuery() {
  try {
    const rawV2 = localStorage.getItem(STORAGE.lastQueryV2);
    if (rawV2) return JSON.parse(rawV2);
    const rawV1 = localStorage.getItem(STORAGE.lastQueryV1);
    if (rawV1) return JSON.parse(rawV1);
  } catch (_) {
    return null;
  }
  return null;
}

function saveUiState() {
  const ui = {
    autoRun: state.autoRun,
    keyboardControls: state.keyboardControls,
    pickOrigin: state.pickOrigin,
    overridesEnabled: state.overridesEnabled,
    moveStepM: state.moveStepM,
    headingDeg: state.headingDeg,
  };
  try {
    localStorage.setItem(STORAGE.uiState, JSON.stringify(ui));
  } catch (_) {
    // Ignore
  }
}

function loadUiState() {
  try {
    const raw = localStorage.getItem(STORAGE.uiState);
    if (!raw) return;
    const ui = JSON.parse(raw);
    if (!ui) return;
    state.autoRun = Boolean(ui.autoRun);
    state.keyboardControls = ui.keyboardControls !== false;
    state.pickOrigin = Boolean(ui.pickOrigin);
    state.overridesEnabled = Boolean(ui.overridesEnabled);
    state.moveStepM = Number(ui.moveStepM) || 50;
    state.headingDeg = Number(ui.headingDeg) || 0;
  } catch (_) {
    // Ignore
  }
}

function setAdvancedOverridesEnabled(enabled) {
  const container = el("advanced-overrides");
  if (!container) return;
  container.querySelectorAll("input, select, textarea").forEach((node) => {
    if (node.id === "enable-overrides") return;
    node.disabled = !enabled;
  });
}

function applyPreset(preset, { isCustom }) {
  if (!preset) return;
  state.applyingPreset = true;
  try {
    const cw = preset.component_weights || {};
    setNumberValue("w-accessibility", cw.accessibility);
    setNumberValue("w-weather", cw.weather);
    setNumberValue("w-preference", cw.preference);
    setNumberValue("w-context", cw.context);

    setNumberValue("avoid-rain", preset.weather_rain_importance);
    setNumberValue("avoid-crowds", preset.avoid_crowds_importance);
    setNumberValue("family-importance", preset.family_friendly_importance);

    const tw = preset.tag_weights || {};
    setNumberValue("tag-indoor", tw.indoor);
    setNumberValue("tag-outdoor", tw.outdoor);
    setNumberValue("tag-culture", tw.culture);
    setNumberValue("tag-food", tw.food);
    setNumberValue("tag-family", tw.family_friendly);
    setNumberValue("tag-crowd", tw.crowd_low);

    setTextValue("required-tags", (preset.required_tags || []).join(", "));
    setTextValue("excluded-tags", (preset.excluded_tags || []).join(", "));

    if (preset.settings_overrides) {
      el("overrides-json").value = JSON.stringify(preset.settings_overrides, null, 2);
      // Best-effort sync of structured advanced inputs.
      const so = preset.settings_overrides;
      setNumberValue("acc-bus-radius", deepGet(so, ["ingestion", "tdx", "accessibility", "radius_m"]));
      setNumberValue("acc-bus-cap", deepGet(so, ["ingestion", "tdx", "accessibility", "count_cap"]));
      setNumberValue(
        "acc-origin-cap",
        deepGet(so, ["ingestion", "tdx", "accessibility", "origin_distance_cap_m"])
      );
    } else if (isCustom) {
      el("overrides-json").value = "";
    }
  } finally {
    state.applyingPreset = false;
  }
}

function setPresetDescription(name) {
  const node = el("preset-description");
  if (!name) {
    node.value = "Custom";
    return;
  }
  const preset = state.customPresets[name] || state.serverPresets[name];
  node.value = preset && preset.description ? preset.description : name;
}

function loadCustomPresets() {
  try {
    const raw = localStorage.getItem(STORAGE.customPresets);
    if (!raw) return;
    const data = JSON.parse(raw);
    if (!data || typeof data !== "object") return;
    state.customPresets = data;
  } catch (_) {
    state.customPresets = {};
  }
}

function saveCustomPresets() {
  try {
    localStorage.setItem(STORAGE.customPresets, JSON.stringify(state.customPresets));
  } catch (_) {
    // Ignore
  }
}

function rebuildPresetSelect() {
  const select = el("preset");
  select.innerHTML = "";

  const custom = document.createElement("option");
  custom.value = "";
  custom.textContent = "Custom";
  select.appendChild(custom);

  const serverNames = Object.keys(state.serverPresets).sort();
  if (serverNames.length) {
    const og = document.createElement("optgroup");
    og.label = "Built-in";
    serverNames.forEach((name) => {
      const opt = document.createElement("option");
      opt.value = name;
      opt.textContent = name;
      og.appendChild(opt);
    });
    select.appendChild(og);
  }

  const customNames = Object.keys(state.customPresets).sort();
  if (customNames.length) {
    const og = document.createElement("optgroup");
    og.label = "Custom (local)";
    customNames.forEach((name) => {
      const opt = document.createElement("option");
      opt.value = name;
      opt.textContent = name;
      og.appendChild(opt);
    });
    select.appendChild(og);
  }
}

async function loadServerPresets() {
  try {
    const data = await fetchJson("/api/presets");
    const presets = data.presets || [];
    state.serverPresets = {};
    presets.forEach((p) => {
      state.serverPresets[p.name] = p;
    });
  } catch (_) {
    state.serverPresets = {};
  }
}

async function loadServerSettings() {
  try {
    state.settings = await fetchJson("/api/settings");
  } catch (_) {
    state.settings = null;
  }
}

function applySettingsDefaults() {
  if (!state.settings) return;

  const s = state.settings;
  const cw = (s.scoring && s.scoring.composite_weights) || {};
  setNumberValue("w-accessibility", cw.accessibility);
  setNumberValue("w-weather", cw.weather);
  setNumberValue("w-preference", cw.preference);
  setNumberValue("w-context", cw.context);

  // Preference defaults come from config when left unset; here we set them explicitly for clarity.
  const wx = (s.ingestion && s.ingestion.weather) || {};
  const wxWeights = wx.score_weights || {};
  setNumberValue("avoid-rain", wxWeights.rain);

  const ctx = (s.features && s.features.context) || {};
  setNumberValue("avoid-crowds", ctx.default_avoid_crowds_importance);
  setNumberValue("family-importance", ctx.default_family_friendly_importance);

  const tags = (s.features && s.features.preference_match && s.features.preference_match.tag_weights_default) || {};
  setNumberValue("tag-indoor", tags.indoor);
  setNumberValue("tag-outdoor", tags.outdoor);
  setNumberValue("tag-culture", tags.culture);
  setNumberValue("tag-food", tags.food);
  setNumberValue("tag-family", tags.family_friendly);
  setNumberValue("tag-crowd", tags.crowd_low);

  // Advanced defaults
  const acc = (s.ingestion && s.ingestion.tdx && s.ingestion.tdx.accessibility) || {};
  setNumberValue("acc-bus-radius", acc.radius_m);
  setNumberValue("acc-bus-cap", acc.count_cap);
  setNumberValue("acc-origin-cap", acc.origin_distance_cap_m);
  const sig = acc.local_transit_signal_weights || {};
  setNumberValue("acc-w-bus", sig.bus);
  setNumberValue("acc-w-metro", sig.metro);
  setNumberValue("acc-w-bike", sig.bike);
  const blend = acc.blend_weights || {};
  setNumberValue("acc-w-local", blend.local_transit);
  setNumberValue("acc-w-origin", blend.origin_proximity);

  const comfort = wx.comfort_temperature_c || {};
  setNumberValue("wx-comfort-min", comfort.min);
  setNumberValue("wx-comfort-max", comfort.max);
  setNumberValue("wx-temp-scale", wx.temperature_penalty_scale_c);
  setNumberValue("wx-w-rain", wxWeights.rain);
  setNumberValue("wx-w-temp", wxWeights.temperature);
  const mult = (s.features && s.features.weather && s.features.weather.rain_importance_multiplier) || {};
  setNumberValue("wx-mult-indoor", mult.indoor);
  setNumberValue("wx-mult-outdoor", mult.outdoor);

  const crowd = (ctx.crowd || {});
  setNumberValue("ctx-weekend-mult", crowd.weekend_multiplier);
  setNumberValue("ctx-parking-weight", crowd.parking_risk_weight);
  const family = (ctx.family || {});
  setNumberValue("ctx-family-bonus", family.tag_bonus);

  const park = (s.features && s.features.parking) || {};
  setNumberValue("park-radius", park.radius_m);
  setNumberValue("park-lot-cap", park.lot_cap);
  setNumberValue("park-spaces-cap", park.available_spaces_cap);
}

function setDefaultTimes() {
  const startEl = el("start");
  const endEl = el("end");
  if (startEl.value) return;

  const now = new Date();
  const start = new Date(now.getTime() + 60 * 60 * 1000);
  const durationH = Number(el("quick-window").value) || 4;
  const end = new Date(start.getTime() + durationH * 60 * 60 * 1000);
  startEl.value = formatLocalDatetime(start);
  endEl.value = formatLocalDatetime(end);
}

function applyQuickWindow() {
  const hours = Number(el("quick-window").value);
  if (!hours) return;
  const startValue = el("start").value;
  if (!startValue) return;
  const start = new Date(startValue);
  if (Number.isNaN(start.getTime())) return;
  const end = new Date(start.getTime() + hours * 60 * 60 * 1000);
  el("end").value = formatLocalDatetime(end);
}

function loadSavedQueryIntoForm(saved) {
  if (!saved || !saved.origin || !saved.time_window) return;

  setNumberValue("origin-lat", saved.origin.lat);
  setNumberValue("origin-lon", saved.origin.lon);
  setTextValue("start", fromTaipeiIso(saved.time_window.start));
  setTextValue("end", fromTaipeiIso(saved.time_window.end));
  setNumberValue("top-n", saved.max_results);

  const cw = saved.component_weights || {};
  setNumberValue("w-accessibility", cw.accessibility);
  setNumberValue("w-weather", cw.weather);
  setNumberValue("w-preference", cw.preference);
  setNumberValue("w-context", cw.context);

  setNumberValue("avoid-rain", saved.weather_rain_importance);
  setNumberValue("avoid-crowds", saved.avoid_crowds_importance);
  setNumberValue("family-importance", saved.family_friendly_importance);

  const tw = saved.tag_weights || {};
  setNumberValue("tag-indoor", tw.indoor);
  setNumberValue("tag-outdoor", tw.outdoor);
  setNumberValue("tag-culture", tw.culture);
  setNumberValue("tag-food", tw.food);
  setNumberValue("tag-family", tw.family_friendly);
  setNumberValue("tag-crowd", tw.crowd_low);

  setTextValue("required-tags", (saved.required_tags || []).join(", "));
  setTextValue("excluded-tags", (saved.excluded_tags || []).join(", "));

  if (saved.settings_overrides) {
    el("overrides-json").value = JSON.stringify(saved.settings_overrides, null, 2);
  }

  const select = el("preset");
  select.value = saved.preset_selection || saved.preset || "";
  setPresetDescription(select.value);
}

let autoRunTimer = null;
function scheduleAutoRun(reason) {
  if (!state.autoRun) return;
  if (autoRunTimer) clearTimeout(autoRunTimer);
  autoRunTimer = setTimeout(() => {
    runRecommendation({ reason: `auto:${reason}` });
  }, 650);
}

async function runRecommendation({ reason } = { reason: "manual" }) {
  const statusPrefix = reason ? `[${reason}] ` : "";
  setStatus(`${statusPrefix}Requesting recommendations…`);

  let payload;
  try {
    payload = buildPreferencesPayload();
  } catch (e) {
    setStatus(`Error: ${e.message}`);
    return;
  }

  saveLastQuery(payload);

  try {
    const resp = await fetch("/api/recommendations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${resp.status}`);
    }
    const data = await resp.json();
    setStatus(`${statusPrefix}Got ${data.results.length} results.`);
    renderResults(data);
    renderMap(data);
  } catch (e) {
    setStatus(`${statusPrefix}Error: ${e.message}`);
  }
}

function onPresetChanged() {
  if (state.applyingPreset) return;
  const name = el("preset").value || "";
  setPresetDescription(name);
  if (!name) {
    el("delete-preset-btn").disabled = true;
    return;
  }
  const isCustom = Boolean(state.customPresets[name]);
  el("delete-preset-btn").disabled = !isCustom;
  const preset = state.customPresets[name] || state.serverPresets[name];
  applyPreset(preset, { isCustom });
  if (state.autoRun) scheduleAutoRun("preset");
}

function resetAll() {
  el("preset").value = "";
  setPresetDescription("");
  el("delete-preset-btn").disabled = true;

  setTextValue("required-tags", "");
  setTextValue("excluded-tags", "");
  el("overrides-json").value = "";
  state.overridesEnabled = false;
  el("enable-overrides").checked = false;
  setAdvancedOverridesEnabled(false);
  saveUiState();

  applySettingsDefaults();
  setDefaultTimes();

  updateOrigin(numberValue("origin-lat") || 25.0478, numberValue("origin-lon") || 121.517, {
    center: true,
    source: "manual",
  });

  if (state.autoRun) scheduleAutoRun("reset");
}

function saveCustomPresetFromForm() {
  const name = window.prompt("Preset name (local):");
  if (!name) return;
  const description = window.prompt("Description (optional):") || "";

  let payload;
  try {
    payload = buildPreferencesPayload();
  } catch (e) {
    setStatus(`Error: ${e.message}`);
    return;
  }

  const preset = {
    description,
    component_weights: payload.component_weights,
    weather_rain_importance: payload.weather_rain_importance,
    avoid_crowds_importance: payload.avoid_crowds_importance,
    family_friendly_importance: payload.family_friendly_importance,
    tag_weights: payload.tag_weights,
    required_tags: payload.required_tags,
    excluded_tags: payload.excluded_tags,
    settings_overrides: payload.settings_overrides,
  };

  state.customPresets[name] = preset;
  saveCustomPresets();
  rebuildPresetSelect();
  el("preset").value = name;
  setPresetDescription(name);
  el("delete-preset-btn").disabled = false;
  setStatus(`Saved preset '${name}'.`);
}

function deleteSelectedPreset() {
  const name = el("preset").value || "";
  if (!name || !state.customPresets[name]) return;
  const ok = window.confirm(`Delete custom preset '${name}'?`);
  if (!ok) return;
  delete state.customPresets[name];
  saveCustomPresets();
  rebuildPresetSelect();
  el("preset").value = "";
  setPresetDescription("");
  el("delete-preset-btn").disabled = true;
  setStatus(`Deleted preset '${name}'.`);
}

function onMove(dir) {
  const step = Number(el("move-step").value) || state.moveStepM || 50;
  state.moveStepM = step;
  saveUiState();

  const lat = numberValue("origin-lat");
  const lon = numberValue("origin-lon");
  if (lat === null || lon === null) return;

  const latRad = (lat * Math.PI) / 180;
  const dLat = step / 111000;
  const dLon = step / (111000 * Math.max(Math.cos(latRad), 0.2));

  let nextLat = lat;
  let nextLon = lon;
  if (dir === "n") nextLat += dLat;
  if (dir === "s") nextLat -= dLat;
  if (dir === "e") nextLon += dLon;
  if (dir === "w") nextLon -= dLon;

  updateOrigin(nextLat, nextLon, { center: false, source: "dpad" });
}

function rotateHeading(delta) {
  state.headingDeg = (Number(state.headingDeg) + delta + 360) % 360;
  setNumberValue("heading-deg", state.headingDeg);
  saveUiState();
  updateHeadingLine();
}

function applySearchFilter() {
  const q = String(el("result-search").value || "").trim().toLowerCase();
  document.querySelectorAll("#results li").forEach((node) => {
    const id = node.dataset.id;
    const item = id ? state.resultsById[id] : null;
    const name = item ? String(item.destination.name || "").toLowerCase() : "";
    const district = item ? String(item.destination.district || "").toLowerCase() : "";
    const visible = !q || name.includes(q) || district.includes(q) || id.toLowerCase().includes(q);
    node.style.display = visible ? "" : "none";
  });
}

function openHelp() {
  const modal = el("help-modal");
  if (modal && typeof modal.showModal === "function") modal.showModal();
}

function closeHelp() {
  const modal = el("help-modal");
  if (modal && typeof modal.close === "function") modal.close();
}

function wireAutoRunListeners() {
  const inputs = document.querySelectorAll(
    "#query-form input, #query-form select, #query-form textarea"
  );
  inputs.forEach((node) => {
    node.addEventListener("change", () => {
      if (state.applyingPreset) return;
      scheduleAutoRun("change");
    });
  });
}

function onGlobalKeyDown(evt) {
  if (evt.key === "?" && !evt.ctrlKey && !evt.metaKey && !evt.altKey) {
    evt.preventDefault();
    openHelp();
    return;
  }

  if (evt.key === "Escape") {
    closeHelp();
    return;
  }

  if (evt.ctrlKey && evt.key === "Enter") {
    evt.preventDefault();
    runRecommendation({ reason: "hotkey" });
    return;
  }

  if (evt.ctrlKey && (evt.key === "r" || evt.key === "R")) {
    evt.preventDefault();
    resetAll();
    return;
  }

  if (evt.ctrlKey && (evt.key === "s" || evt.key === "S")) {
    evt.preventDefault();
    saveCustomPresetFromForm();
    return;
  }

  if (isTypingTarget(evt.target)) return;

  if (evt.key === "a" || evt.key === "A") {
    state.autoRun = !state.autoRun;
    el("auto-run").checked = state.autoRun;
    saveUiState();
    setStatus(`Auto-run ${state.autoRun ? "enabled" : "disabled"}.`);
    return;
  }

  if (evt.key === "m" || evt.key === "M") {
    state.pickOrigin = !state.pickOrigin;
    el("pick-origin").checked = state.pickOrigin;
    saveUiState();
    setStatus(`Pick origin ${state.pickOrigin ? "enabled" : "disabled"}.`);
    return;
  }

  if (evt.key === "k" || evt.key === "K") {
    state.keyboardControls = !state.keyboardControls;
    el("keyboard-controls").checked = state.keyboardControls;
    saveUiState();
    setStatus(`Keyboard controls ${state.keyboardControls ? "enabled" : "disabled"}.`);
    return;
  }

  if (/^[1-9]$/.test(evt.key) && state.resultOrder.length) {
    const idx = Number(evt.key) - 1;
    const id = state.resultOrder[idx];
    if (id) {
      evt.preventDefault();
      selectResult(id);
      return;
    }
  }

  if (!state.keyboardControls) return;

  const moveKeys = {
    ArrowUp: "n",
    ArrowDown: "s",
    ArrowLeft: "w",
    ArrowRight: "e",
    w: "n",
    a: "w",
    s: "s",
    d: "e",
    W: "n",
    A: "w",
    S: "s",
    D: "e",
  };

  if (evt.key in moveKeys) {
    evt.preventDefault();
    onMove(moveKeys[evt.key]);
    return;
  }

  if (evt.key === "[" || evt.key === "{") {
    evt.preventDefault();
    const options = Array.from(el("move-step").options).map((o) => Number(o.value));
    const idx = options.indexOf(Number(el("move-step").value));
    const next = options[Math.max(0, idx - 1)] || options[0];
    el("move-step").value = String(next);
    state.moveStepM = next;
    saveUiState();
    setStatus(`Move step: ${next}m.`);
    return;
  }

  if (evt.key === "]" || evt.key === "}") {
    evt.preventDefault();
    const options = Array.from(el("move-step").options).map((o) => Number(o.value));
    const idx = options.indexOf(Number(el("move-step").value));
    const next = options[Math.min(options.length - 1, idx + 1)] || options[options.length - 1];
    el("move-step").value = String(next);
    state.moveStepM = next;
    saveUiState();
    setStatus(`Move step: ${next}m.`);
    return;
  }

  if (evt.key === "q" || evt.key === "Q") {
    evt.preventDefault();
    rotateHeading(-15);
    return;
  }

  if (evt.key === "e" || evt.key === "E") {
    evt.preventDefault();
    rotateHeading(15);
    return;
  }

  if (evt.key === "c" || evt.key === "C") {
    evt.preventDefault();
    const lat = numberValue("origin-lat");
    const lon = numberValue("origin-lon");
    if (lat !== null && lon !== null && state.map) state.map.setView([lat, lon], Math.max(state.map.getZoom(), 12));
  }
}

function initDom() {
  el("query-form").addEventListener("submit", (e) => {
    e.preventDefault();
    runRecommendation({ reason: "submit" });
  });
  el("run-btn").addEventListener("click", () => runRecommendation({ reason: "run" }));
  el("reset-btn").addEventListener("click", resetAll);
  el("save-preset-btn").addEventListener("click", saveCustomPresetFromForm);
  el("delete-preset-btn").addEventListener("click", deleteSelectedPreset);
  el("help-btn").addEventListener("click", openHelp);
  el("clear-btn").addEventListener("click", () => {
    clearMarkers();
    el("results").innerHTML = "";
    el("inspector").innerHTML = `<p class="hint">Cleared.</p>`;
    setStatus("Cleared results and markers.");
  });

  el("preset").addEventListener("change", onPresetChanged);

  el("auto-run").addEventListener("change", (e) => {
    state.autoRun = Boolean(e.target.checked);
    saveUiState();
  });
  el("pick-origin").addEventListener("change", (e) => {
    state.pickOrigin = Boolean(e.target.checked);
    saveUiState();
  });
  el("enable-overrides").addEventListener("change", (e) => {
    state.overridesEnabled = Boolean(e.target.checked);
    setAdvancedOverridesEnabled(state.overridesEnabled);
    saveUiState();
    if (state.autoRun) scheduleAutoRun("overrides");
  });
  el("keyboard-controls").addEventListener("change", (e) => {
    state.keyboardControls = Boolean(e.target.checked);
    saveUiState();
  });
  el("move-step").addEventListener("change", (e) => {
    state.moveStepM = Number(e.target.value) || 50;
    saveUiState();
  });

  el("heading-deg").addEventListener("change", () => {
    state.headingDeg = clamp(Number(numberValue("heading-deg") || 0), 0, 359);
    saveUiState();
    updateHeadingLine();
  });
  el("rotate-left").addEventListener("click", () => rotateHeading(-15));
  el("rotate-right").addEventListener("click", () => rotateHeading(15));
  el("center-origin-btn").addEventListener("click", () => {
    const lat = numberValue("origin-lat");
    const lon = numberValue("origin-lon");
    if (lat !== null && lon !== null) updateOrigin(lat, lon, { center: true, source: "manual" });
  });

  el("move-n").addEventListener("click", () => onMove("n"));
  el("move-s").addEventListener("click", () => onMove("s"));
  el("move-e").addEventListener("click", () => onMove("e"));
  el("move-w").addEventListener("click", () => onMove("w"));

  el("quick-window").addEventListener("change", () => {
    applyQuickWindow();
    if (state.autoRun) scheduleAutoRun("time_window");
  });
  el("start").addEventListener("change", () => {
    applyQuickWindow();
    if (state.autoRun) scheduleAutoRun("time_window");
  });
  el("end").addEventListener("change", () => {
    if (state.autoRun) scheduleAutoRun("time_window");
  });

  el("origin-lat").addEventListener("change", () => {
    const lat = numberValue("origin-lat");
    const lon = numberValue("origin-lon");
    if (lat !== null && lon !== null) updateOrigin(lat, lon, { center: false, source: "manual" });
  });
  el("origin-lon").addEventListener("change", () => {
    const lat = numberValue("origin-lat");
    const lon = numberValue("origin-lon");
    if (lat !== null && lon !== null) updateOrigin(lat, lon, { center: false, source: "manual" });
  });

  el("result-search").addEventListener("input", applySearchFilter);

  el("copy-query-btn").addEventListener("click", async () => {
    try {
      const payload = buildPreferencesPayload();
      const ok = await copyText(JSON.stringify(payload, null, 2));
      setStatus(ok ? "Copied request JSON." : "Copy failed.");
    } catch (e) {
      setStatus(`Error: ${e.message}`);
    }
  });

  el("copy-selected-btn").addEventListener("click", async () => {
    const id = state.selectedId;
    if (!id || !(id in state.resultsById)) {
      setStatus("No selected result.");
      return;
    }
    const ok = await copyText(JSON.stringify(state.resultsById[id], null, 2));
    setStatus(ok ? "Copied selected item JSON." : "Copy failed.");
  });

  el("load-defaults-btn").addEventListener("click", () => {
    applySettingsDefaults();
    el("overrides-json").value = "";
    setStatus("Loaded server defaults.");
    if (state.autoRun) scheduleAutoRun("defaults");
  });

  document.addEventListener("keydown", onGlobalKeyDown);
  wireAutoRunListeners();
}

(async function init() {
  loadUiState();
  el("auto-run").checked = state.autoRun;
  el("pick-origin").checked = state.pickOrigin;
  el("enable-overrides").checked = state.overridesEnabled;
  setAdvancedOverridesEnabled(state.overridesEnabled);
  el("keyboard-controls").checked = state.keyboardControls;
  el("move-step").value = String(state.moveStepM || 50);
  setNumberValue("heading-deg", state.headingDeg || 0);

  await loadServerSettings();
  await loadServerPresets();
  loadCustomPresets();
  rebuildPresetSelect();

  applySettingsDefaults();

  const saved = loadLastQuery();
  loadSavedQueryIntoForm(saved);
  setDefaultTimes();

  setPresetDescription(el("preset").value || "");
  el("delete-preset-btn").disabled = !Boolean(state.customPresets[el("preset").value || ""]);

  const lat = numberValue("origin-lat") || 25.0478;
  const lon = numberValue("origin-lon") || 121.517;
  ensureMap(lat, lon);
  updateOrigin(lat, lon, { center: true, source: "manual" });

  initDom();
  setStatus("Ready. Press Ctrl+Enter to run.");
})();
