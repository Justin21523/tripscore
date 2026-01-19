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

function escapeHtml(s) {
  return String(s || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
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
  tdxJobId: "tripscore.tdx_job_id.v1",
};

const state = {
  applyingPreset: false,
  autoRun: false,
  keyboardControls: true,
  pickOrigin: false,
  overridesEnabled: false,
  moveStepM: 50,
  headingDeg: 0,
  activeTab: "results",
  showLines: false,
  settings: null,
  serverPresets: {},
  customPresets: {},
  lastResponse: null,
  resultsById: {},
  baseOrder: [],
  viewOrder: [],
  baseRankById: {},
  selectedId: null,
  map: null,
  originMarker: null,
  headingLine: null,
  routeLine: null,
  routeLines: [],
  destMarkers: {},
  markerGroup: null,
  activeSetupStep: "step-1",
  tdxStatus: null,
  qualityReport: null,
  catalogMeta: null,
  tdxJobId: null,
  tdxJob: null,
};

function setStatus(message) {
  el("status").textContent = message;
}

function renderPolicySummary() {
  const node = el("policy-summary");
  if (!node) return;

  const resp = state.lastResponse;
  if (!resp || !Array.isArray(resp.results) || resp.results.length === 0) {
    node.innerHTML = "";
    return;
  }

  const top = resp.results.slice(0, 3);
  const errors = countTdxErrors(resp.results);
  const warnings = resp.meta && Array.isArray(resp.meta.warnings) ? resp.meta.warnings : [];
  const cache = resp.meta && resp.meta.cache ? resp.meta.cache : null;

  const report = state.qualityReport;
  const qualitySeverity = (report && report.overall && report.overall.severity) || "info";
  const qualityBadge =
    qualitySeverity === "error"
      ? `<span class="badge bad">error</span>`
      : qualitySeverity === "warning"
      ? `<span class="badge warn">warning</span>`
      : `<span class="badge ok">info</span>`;

  const when =
    resp.query && resp.query.start && resp.query.end ? `${resp.query.start} → ${resp.query.end}` : null;

  const lines = [];
  lines.push(`<h3>Policy Brief</h3>`);
  lines.push(
    `<p class="lead">A decision-focused summary of the current recommendation run${
      when ? ` for <span class="badge">${escapeHtml(when)}</span>` : ""
    }.</p>`
  );

  lines.push("<h4>Executive Summary</h4>");
  lines.push("<ul>");
  top.forEach((it, idx) => {
    const name = it.destination && it.destination.name ? it.destination.name : `Result ${idx + 1}`;
    const score =
      it.breakdown && typeof it.breakdown.total_score === "number" ? it.breakdown.total_score : null;
    const tagCount = it.destination && Array.isArray(it.destination.tags) ? it.destination.tags.length : 0;
    lines.push(
      `<li><strong>#${idx + 1}</strong> ${escapeHtml(name)}${
        score !== null ? ` (score ${Number(score).toFixed(3)})` : ""
      }${tagCount ? ` · ${tagCount} tags` : ""}</li>`
    );
  });
  lines.push("</ul>");

  lines.push("<h4>Evidence & Data Quality</h4>");
  lines.push("<ul>");
  lines.push(`<li>Quality report: ${qualityBadge}</li>`);
  lines.push(
    `<li>Transit signal errors in this run: <span class="badge ${
      errors ? "warn" : "ok"
    }">${errors}</span></li>`
  );
  if (warnings.length) lines.push(`<li>Server warnings: <span class="badge warn">${warnings.length}</span></li>`);
  if (cache) {
    const hits = Number(cache.hits) || 0;
    const misses = Number(cache.misses) || 0;
    const stale = Number(cache.stale_fallbacks) || 0;
    lines.push(`<li>Cache: hit ${hits} · miss ${misses} · stale ${stale}</li>`);
  }
  lines.push("</ul>");

  lines.push("<h4>Recommended Next Actions</h4>");
  lines.push("<ul>");
  lines.push(
    "<li>Confirm your starting point and time window (small shifts can change accessibility and crowd risk).</li>"
  );
  lines.push("<li>If constraints are strict, use presets and re-run to compare scenarios.</li>");
  lines.push("<li>Open a result to see the structured brief and data limitations.</li>");
  lines.push("</ul>");

  node.innerHTML = lines.join("");
}

function setBusy(busy, message) {
  const runBtn = el("run-btn");
  const submitBtn = el("submit");
  if (runBtn) runBtn.disabled = busy;
  if (submitBtn) submitBtn.disabled = busy;
  if (message) setStatus(message);
}

async function fetchJson(url) {
  const resp = await fetch(url);
  if (!resp.ok) {
    let payload = null;
    try {
      payload = await resp.json();
    } catch (_) {
      // ignore
    }
    const detail = payload && payload.detail !== undefined ? payload.detail : payload;
    const msg = apiErrorMessage({ status: resp.status, detail });
    const err = new Error(msg);
    err.status = resp.status;
    err.detail = detail;
    throw err;
  }
  return await resp.json();
}

function apiErrorMessage({ status, detail }) {
  if (detail && typeof detail === "object") {
    if (detail.message) return `${detail.code ? `${detail.code}: ` : ""}${detail.message}`;
    try {
      return `HTTP ${status}: ${JSON.stringify(detail)}`;
    } catch (_) {
      return `HTTP ${status}`;
    }
  }
  if (typeof detail === "string" && detail.trim()) return `HTTP ${status}: ${detail}`;
  return `HTTP ${status}`;
}

function haversineMeters(aLat, aLon, bLat, bLon) {
  const toRad = (d) => (d * Math.PI) / 180;
  const R = 6371000;
  const dLat = toRad(bLat - aLat);
  const dLon = toRad(bLon - aLon);
  const lat1 = toRad(aLat);
  const lat2 = toRad(bLat);
  const s =
    Math.sin(dLat / 2) * Math.sin(dLat / 2) +
    Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLon / 2) * Math.sin(dLon / 2);
  return 2 * R * Math.asin(Math.min(1, Math.sqrt(s)));
}

function formatMeters(m) {
  if (m === null || m === undefined) return "";
  const meters = Number(m);
  if (!Number.isFinite(meters)) return "";
  if (meters < 1000) return `${Math.round(meters)}m`;
  return `${(meters / 1000).toFixed(2)}km`;
}

function formatSecondsShort(seconds) {
  const s = Number(seconds);
  if (!Number.isFinite(s) || s < 0) return "—";
  if (s < 90) return `${Math.round(s)}s`;
  return `${Math.round(s / 60)} min`;
}

function topComponents(item, n = 2) {
  const comps = (item.breakdown && item.breakdown.components) || [];
  return [...comps]
    .sort((a, b) => (Number(b.contribution) || 0) - (Number(a.contribution) || 0))
    .slice(0, n);
}

function componentLabel(name) {
  const m = {
    accessibility: "Getting there",
    weather: "Weather comfort",
    preference: "Fit to your preferences",
    context: "Crowd & family context",
  };
  return m[name] || name;
}

function formatUnix(unixSeconds) {
  if (!unixSeconds) return "";
  try {
    const d = new Date(Number(unixSeconds) * 1000);
    return d.toLocaleString();
  } catch (_) {
    return "";
  }
}

function buildStory(item) {
  const dest = item.destination;
  const total = Number(item.breakdown.total_score) || 0;
  const comps = topComponents(item, 2);
  const lines = [];

  lines.push(`Overall suitability score is ${total.toFixed(3)} (0–1 scale).`);
  if (dest.tags && dest.tags.length) {
    lines.push(`This place is tagged: ${dest.tags.slice(0, 6).join(", ")}.`);
  }

  comps.forEach((c) => {
    const reasons = (c.reasons || []).slice(0, 2).filter(Boolean);
    if (reasons.length) {
      lines.push(`${componentLabel(c.name)} mattered most: ${reasons.join("; ")}.`);
    } else {
      lines.push(
        `${componentLabel(c.name)} mattered most: score ${Number(c.score).toFixed(3)} × weight ${Number(
          c.weight
        ).toFixed(2)}.`
      );
    }
  });

  const errors =
    item.breakdown &&
    item.breakdown.components &&
    item.breakdown.components
      .map((c) => (c.details && c.details.tdx_errors ? c.details.tdx_errors : null))
      .filter(Boolean);
  if (errors && errors.length) {
    lines.push("Some real-time transit signals were unavailable; results may be conservative.");
  }

  return lines.slice(0, 5);
}

function buildPolicyBrief(item) {
  const dest = item.destination;
  const resp = state.lastResponse;
  const prefs = resp && resp.query ? resp.query : null;
  const comps = (item.breakdown && item.breakdown.components) || [];
  const byName = {};
  comps.forEach((c) => {
    byName[c.name] = c;
  });

  const reasons = topComponents(item, 2)
    .map((c) => {
      const r = (c.reasons || []).slice(0, 2).filter(Boolean);
      if (r.length) return `${componentLabel(c.name)}: ${r.join("; ")}`;
      return `${componentLabel(c.name)}: score ${Number(c.score).toFixed(2)} × w ${Number(c.weight).toFixed(2)}`;
    })
    .slice(0, 2);

  const risks = [];
  const tdxErrs = [];
  comps.forEach((c) => {
    const e = c.details && c.details.tdx_errors;
    if (e && typeof e === "object") {
      Object.entries(e).forEach(([k, v]) => tdxErrs.push(`${k}: ${String(v).slice(0, 120)}`));
    }
  });
  if (tdxErrs.length) risks.push(`Transit data degraded (${tdxErrs.length} signals): ${tdxErrs.slice(0, 2).join(" · ")}`);

  // Global data quality hints (offline coverage + daemon metrics).
  try {
    const report = state.qualityReport;
    const cov = report && report.tdx && report.tdx.bulk_coverage;
    const worst = report && report.overall && report.overall.severity;
    if (worst === "error") risks.push("Data quality report indicates errors; some signals may be missing or outdated.");
    if (worst === "warning") risks.push("Data quality report indicates warnings; some datasets may be incomplete.");

    const d = state.tdxStatus && state.tdxStatus.daemon && state.tdxStatus.daemon.daemon;
    if (d && d.global_cooldown_until_unix) {
      const now = Math.floor(Date.now() / 1000);
      if (now < Number(d.global_cooldown_until_unix)) risks.push("TDX is in cooldown due to upstream rate limits; real-time signals may be delayed.");
    }

    if (cov && cov.summary && cov.summary.by_city && dest && dest.city) {
      const normalize = (s) => String(s || "").toLowerCase().replaceAll(" ", "").replaceAll("_", "");
      const want = normalize(dest.city);
      const byCity = cov.summary.by_city || {};
      let match = null;
      Object.keys(byCity).forEach((c) => {
        if (!match && normalize(c) === want) match = c;
      });
      if (match) {
        const st = byCity[match] || {};
        const inc = Number(st.incomplete || 0) + Number(st.missing || 0);
        const rl = Number(st.error_429 || 0);
        if (inc > 0 || rl > 0) risks.push(`TDX bulk coverage for ${match} is still in progress (incomplete/missing ${inc}, 429 ${rl}).`);
      }
    }
  } catch (_) {
    // ignore
  }
  const wx = byName.weather;
  if (wx && wx.details) {
    if (wx.details.max_precipitation_probability === null) risks.push("Rain probability unavailable (weather is more uncertain).");
    if (wx.details.mean_temperature_c === null) risks.push("Temperature unavailable (weather is more uncertain).");
  }
  const ctx = byName.context;
  if (ctx && ctx.details && typeof ctx.details.predicted_crowd_risk === "number") {
    const r = Number(ctx.details.predicted_crowd_risk);
    if (r > 0.66) risks.push("Crowd risk appears high for this time window.");
  }

  const tags = new Set((dest.tags || []).map((t) => String(t).toLowerCase()));
  const suited = [];
  const notSuited = [];
  if (tags.has("indoor")) suited.push("People who prefer indoor plans.");
  if (tags.has("outdoor")) suited.push("People who prefer outdoor plans (weather-sensitive).");
  if (tags.has("family_friendly")) suited.push("Families with kids.");
  if (tags.has("crowd_low")) suited.push("People who prefer calmer places.");

  if (prefs && prefs.weather_rain_importance !== null && Number(prefs.weather_rain_importance) >= 0.75 && tags.has("outdoor") && !tags.has("indoor")) {
    notSuited.push("If avoiding rain is critical, outdoor-first places may disappoint on wet days.");
  }
  if (prefs && prefs.avoid_crowds_importance !== null && Number(prefs.avoid_crowds_importance) >= 0.75 && !tags.has("crowd_low")) {
    notSuited.push("If avoiding crowds is critical, this may not be the safest bet at peak times.");
  }

  const actions = [];
  actions.push("Use the map to pick an origin closer to where you’ll actually start.");
  actions.push("Adjust the time window (weekday vs weekend can change crowd risk).");
  actions.push("If you want a different style, try a mode (Balanced / Rainy day / Family) then re-run.");

  return {
    reasons,
    risks: risks.length ? risks : ["No major risks detected from available signals."],
    suited: suited.length ? suited.slice(0, 4) : ["General audiences (no strong constraints detected)."],
    notSuited: notSuited.length ? notSuited.slice(0, 4) : ["No strong mismatch detected."],
    actions: actions.slice(0, 4),
  };
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
  if (state.markerGroup) {
    state.markerGroup.remove();
    state.markerGroup = null;
  }
  Object.values(state.destMarkers).forEach((m) => m.remove());
  state.destMarkers = {};
  if (state.routeLine) {
    state.routeLine.remove();
    state.routeLine = null;
  }
  if (state.routeLines && state.routeLines.length) {
    state.routeLines.forEach((l) => l.remove());
    state.routeLines = [];
  }
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
  updateBriefStrip();

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
  updateRouteLineForSelected();
  updateRouteLinesForAll();
  if (source === "keyboard" || source === "dpad" || source === "map_click") {
    if (state.autoRun) scheduleAutoRun("origin_move");
  }
}

function selectResult(id, { focusTab } = { focusTab: true }) {
  if (!id || !(id in state.resultsById)) return;
  state.selectedId = id;
  const item = state.resultsById[id];

  const inspector = el("inspector");
  inspector.innerHTML = "";

  const dest = item.destination;
  const breakdown = item.breakdown;

  const hero = document.createElement("div");
  hero.className = "hero";

  const title = document.createElement("div");
  title.className = "title";
  title.textContent = dest.name;

  const score = document.createElement("div");
  score.className = "hero-score";
  score.innerHTML = `total <strong>${Number(breakdown.total_score).toFixed(3)}</strong>`;

  hero.appendChild(title);
  hero.appendChild(score);

  const meta = document.createElement("div");
  meta.className = "meta";
  meta.textContent = `${dest.city || ""} ${dest.district || ""}`.trim();

  const sub = document.createElement("div");
  sub.className = "submeta";
  const originLat = numberValue("origin-lat");
  const originLon = numberValue("origin-lon");
  if (originLat !== null && originLon !== null) {
    const d = haversineMeters(originLat, originLon, dest.location.lat, dest.location.lon);
    const pill = document.createElement("span");
    pill.className = "pill muted";
    pill.textContent = `origin → dest: ${formatMeters(d)}`;
    sub.appendChild(pill);
  }
  if (dest.description) {
    const pill = document.createElement("span");
    pill.className = "pill muted";
    pill.textContent = String(dest.description).slice(0, 140);
    sub.appendChild(pill);
  }
  if (dest.opening_hours) {
    const pill = document.createElement("span");
    pill.className = "pill muted";
    pill.textContent = `hours: ${String(dest.opening_hours).slice(0, 120)}`;
    sub.appendChild(pill);
  }
  if (dest.address) {
    const pill = document.createElement("span");
    pill.className = "pill muted";
    pill.textContent = `address: ${String(dest.address).slice(0, 140)}`;
    sub.appendChild(pill);
  }
  if (dest.phone) {
    const pill = document.createElement("span");
    pill.className = "pill muted";
    pill.textContent = `phone: ${String(dest.phone).slice(0, 80)}`;
    sub.appendChild(pill);
  }

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

  inspector.appendChild(hero);
  inspector.appendChild(meta);
  if (sub.childNodes.length) inspector.appendChild(sub);
  if ((dest.tags || []).length > 0) inspector.appendChild(tags);
  if (dest.url) inspector.appendChild(link);

  const story = document.createElement("div");
  story.className = "story";
  const storyTitle = document.createElement("h3");
  storyTitle.textContent = "Policy-style brief";
  const storyLead = document.createElement("p");
  storyLead.textContent = "A structured summary designed for quick decision-making and transparency.";

  const sections = buildPolicyBrief(item);
  const block = (title, items) => {
    const h = document.createElement("h4");
    h.textContent = title;
    const u = document.createElement("ul");
    (items || []).forEach((t) => {
      const li = document.createElement("li");
      li.textContent = t;
      u.appendChild(li);
    });
    return [h, u];
  };

  story.appendChild(storyTitle);
  story.appendChild(storyLead);
  block("Why we recommend it", sections.reasons).forEach((n) => story.appendChild(n));
  block("Risks & limitations", sections.risks).forEach((n) => story.appendChild(n));
  block("Good fit for", sections.suited).forEach((n) => story.appendChild(n));
  block("Not ideal for", sections.notSuited).forEach((n) => story.appendChild(n));
  block("Recommended next actions", sections.actions).forEach((n) => story.appendChild(n));
  inspector.appendChild(story);

  const busEtaCard = document.createElement("div");
  busEtaCard.className = "story bus-eta";
  busEtaCard.dataset.destId = dest.id;
  const busEtaTitle = document.createElement("h3");
  busEtaTitle.textContent = "Nearby bus arrivals (TDX)";
  const busEtaLead = document.createElement("p");
  busEtaLead.textContent = "Real-time estimates for a few nearby stops. Subject to upstream rate limits.";
  const busEtaBody = document.createElement("div");
  busEtaBody.className = "bus-eta-body";
  busEtaBody.textContent = "Loading…";
  busEtaCard.appendChild(busEtaTitle);
  busEtaCard.appendChild(busEtaLead);
  busEtaCard.appendChild(busEtaBody);
  inspector.appendChild(busEtaCard);

  (async () => {
    try {
      const data = await fetchJson(
        `/api/tdx/bus/eta/nearby?lat=${encodeURIComponent(dest.location.lat)}&lon=${encodeURIComponent(
          dest.location.lon
        )}&city=${encodeURIComponent(dest.city || "")}&radius_m=450&max_stops=8&max_rows=24`
      );
      if (busEtaCard.dataset.destId !== dest.id) return;
      const eta = (data && data.eta) || [];
      if (!eta.length) {
        busEtaBody.textContent = "No upcoming buses found for nearby stops (or data unavailable).";
        return;
      }
      busEtaBody.innerHTML = "";

      const summary = (data && data.summary) || {};
      const routes = summary.routes || [];
      if (Array.isArray(routes) && routes.length) {
        const h = document.createElement("h4");
        h.textContent = "Route summaries";
        const ul = document.createElement("ul");
        routes.slice(0, 8).forEach((r) => {
          const li = document.createElement("li");
          const soonest = r.soonest_seconds !== null && r.soonest_seconds !== undefined ? formatSecondsShort(r.soonest_seconds) : "—";
          const headway =
            r.headway_seconds !== null && r.headway_seconds !== undefined ? ` · headway ${formatSecondsShort(r.headway_seconds)}` : "";
          const name = r.route_name || r.route_uid || "route";
          const stop = r.example_stop_name ? ` @ ${r.example_stop_name}` : "";
          const dir = r.direction !== null && r.direction !== undefined ? ` (dir ${r.direction})` : "";
          li.textContent = `${soonest}${headway} · ${name}${dir}${stop}`;
          ul.appendChild(li);
        });

        const top = routes[0];
        if (top && top.route_uid) {
          const btn = document.createElement("button");
          btn.type = "button";
          btn.className = "btn btn-small";
          btn.textContent = "Show stops for top route";
          btn.addEventListener("click", async () => {
            try {
              btn.disabled = true;
              btn.textContent = "Loading stops…";
              const resp = await fetchJson(
                `/api/tdx/bus/stop_of_route?city=${encodeURIComponent(dest.city || "")}&route_uid=${encodeURIComponent(
                  top.route_uid
                )}${top.direction !== null && top.direction !== undefined ? `&direction=${encodeURIComponent(String(top.direction))}` : ""}`
              );
              const stops = (resp && resp.stops) || [];
              const list = document.createElement("ul");
              stops.slice(0, 24).forEach((s) => {
                const li = document.createElement("li");
                li.textContent = `${s.sequence !== null && s.sequence !== undefined ? `${s.sequence}. ` : ""}${s.stop_name || s.stop_uid}`;
                list.appendChild(li);
              });
              busEtaBody.appendChild(document.createElement("hr"));
              const h2 = document.createElement("h4");
              h2.textContent = "Stops (sample)";
              busEtaBody.appendChild(h2);
              busEtaBody.appendChild(list);
              btn.remove();
            } catch (e) {
              btn.textContent = `Stops unavailable: ${e.message}`;
            }
          });
          busEtaBody.appendChild(btn);
        }

        busEtaBody.appendChild(h);
        busEtaBody.appendChild(ul);
      }

      const ul2 = document.createElement("ul");
      eta.slice(0, 10).forEach((e) => {
        const li = document.createElement("li");
        const stop = e.stop_name ? `@ ${e.stop_name}` : "";
        const route = e.route_name || e.route_uid || "route";
        li.textContent = `${formatSecondsShort(e.estimate_seconds)} · ${route} ${stop}`;
        ul2.appendChild(li);
      });
      const hRaw = document.createElement("h4");
      hRaw.textContent = "Raw ETAs";
      busEtaBody.appendChild(hRaw);
      busEtaBody.appendChild(ul2);
    } catch (e) {
      if (busEtaCard.dataset.destId !== dest.id) return;
      busEtaBody.textContent = `TDX bus ETA unavailable: ${e.message}`;
    }
  })();

  inspector.appendChild(scorebarForItem(item));

  const grid = document.createElement("div");
  grid.className = "component-grid";
  breakdown.components.forEach((c) => {
    const card = document.createElement("div");
    card.className = "comp-card";

    const head = document.createElement("div");
    head.className = "comp-name";
    head.textContent = c.name;
    const badge = document.createElement("span");
    badge.className = "pill";
    badge.textContent = `+${Number(c.contribution).toFixed(3)}`;
    head.appendChild(badge);

    const metrics = document.createElement("div");
    metrics.className = "comp-metrics";
    metrics.textContent = `score ${Number(c.score).toFixed(3)} · w ${Number(c.weight).toFixed(2)}`;

    card.appendChild(head);
    card.appendChild(metrics);
    card.addEventListener("click", () => {
      const node = inspector.querySelector(`[data-comp-section="${CSS.escape(c.name)}"]`);
      if (node) node.scrollIntoView({ behavior: "smooth", block: "start" });
    });
    grid.appendChild(card);
  });
  inspector.appendChild(grid);

  breakdown.components.forEach((c) => {
    const d = document.createElement("details");
    d.open = c.name === "accessibility";
    d.dataset.compSection = c.name;
    const s = document.createElement("summary");
    s.textContent = `${c.name}: score ${Number(c.score).toFixed(3)} × w ${Number(c.weight).toFixed(
      2
    )} → ${Number(c.contribution).toFixed(3)}`;
    d.appendChild(s);

    const body = document.createElement("div");
    body.className = "section-body";

    const detailsObj = c.details || {};
    if (detailsObj && typeof detailsObj === "object" && !Array.isArray(detailsObj)) {
      const errors = detailsObj.tdx_errors;
      if (errors && typeof errors === "object" && Object.keys(errors).length) {
        const alert = document.createElement("div");
        alert.className = "alert";
        const t = document.createElement("div");
        t.className = "alert-title";
        t.textContent = "Data degraded (upstream unavailable)";
        const b = document.createElement("div");
        b.className = "alert-body";
        b.textContent = Object.entries(errors)
          .map(([k, v]) => `${k}: ${String(v).slice(0, 160)}`)
          .join(" · ");
        alert.appendChild(t);
        alert.appendChild(b);
        body.appendChild(alert);
      }
    }

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

    const kv = keyValueTable(detailsObj);
    if (kv) body.appendChild(kv);

    const raw = document.createElement("details");
    raw.className = "raw-toggle";
    const rawSum = document.createElement("summary");
    rawSum.textContent = "Raw details (JSON)";
    raw.appendChild(rawSum);
    const pre = document.createElement("pre");
    pre.textContent = JSON.stringify(c.details || {}, null, 2);
    raw.appendChild(pre);
    body.appendChild(raw);

    d.appendChild(body);
    inspector.appendChild(d);
  });

  renderDebug();
  if (focusTab) openTab("details");

  document.querySelectorAll("#results li").forEach((node) => node.classList.remove("selected"));
  const selectedNode = document.querySelector(`#results li[data-id="${CSS.escape(id)}"]`);
  if (selectedNode) selectedNode.classList.add("selected");

  highlightMarker(id);
  if (state.map && state.destMarkers[id]) {
    const marker = state.destMarkers[id];
    if (state.markerGroup && typeof state.markerGroup.zoomToShowLayer === "function") {
      state.markerGroup.zoomToShowLayer(marker, () => marker.openPopup());
    } else {
      marker.openPopup();
    }
  }
  updateRouteLineForSelected();
}

function isPrimitive(v) {
  return v === null || v === undefined || ["string", "number", "boolean"].includes(typeof v);
}

function keyValueTable(detailsObj) {
  if (!detailsObj || typeof detailsObj !== "object" || Array.isArray(detailsObj)) return null;
  const rows = [];
  Object.entries(detailsObj).forEach(([k, v]) => {
    if (!isPrimitive(v)) return;
    const s = String(v);
    if (s.length > 160) return;
    rows.push([k, s]);
  });
  if (!rows.length) return null;

  const wrap = document.createElement("div");
  wrap.className = "kv";
  rows.slice(0, 18).forEach(([k, v]) => {
    const row = document.createElement("div");
    row.className = "kv-row";
    const kk = document.createElement("div");
    kk.className = "k";
    kk.textContent = k;
    const vv = document.createElement("div");
    vv.className = "v";
    vv.textContent = v;
    row.appendChild(kk);
    row.appendChild(vv);
    wrap.appendChild(row);
  });
  return wrap;
}

function scorebarForItem(item) {
  const bar = document.createElement("div");
  bar.className = "scorebar";
  const comps = (item.breakdown && item.breakdown.components) || [];
  const total = Number(item.breakdown.total_score) || 0;

  const seg = (name, cls) => {
    const c = comps.find((x) => x.name === name);
    const contrib = c ? Number(c.contribution) : 0;
    const w = total > 0 ? (contrib / total) * 100 : 0;
    const span = document.createElement("span");
    span.className = cls;
    span.style.width = `${Math.max(0, Math.min(100, w))}%`;
    span.title = `${name}: ${contrib.toFixed(3)}`;
    return span;
  };

  bar.appendChild(seg("accessibility", "seg-accessibility"));
  bar.appendChild(seg("weather", "seg-weather"));
  bar.appendChild(seg("preference", "seg-preference"));
  bar.appendChild(seg("context", "seg-context"));
  return bar;
}

function setResultsPayload(payload) {
  state.lastResponse = payload;
  state.resultsById = {};
  state.baseOrder = [];
  state.viewOrder = [];
  state.baseRankById = {};

  (payload.results || []).forEach((item, idx) => {
    const id = item.destination.id;
    state.resultsById[id] = item;
    state.baseOrder.push(id);
    state.baseRankById[id] = idx + 1;
  });

  updateBriefStrip();
  renderPolicySummary();
  updateView({ selectDefault: true });
}

function highlightMarker(id) {
  Object.entries(state.destMarkers).forEach(([destId, marker]) => {
    const rank = marker.options.__rank || 0;
    marker.setIcon(destIcon(rank, destId === id));
  });
}

function buildResultListItem(item, { rank }) {
  const li = document.createElement("li");
  li.dataset.id = item.destination.id;

  const dest = item.destination;
  const breakdown = item.breakdown;

  const title = document.createElement("div");
  title.className = "result-title";
  const row = document.createElement("div");
  row.className = "result-row";
  title.textContent = `${rank || ""}. ${dest.name}`.replace(/^\\. /, "");

  const score = document.createElement("div");
  score.className = "result-score";
  score.innerHTML = `score <strong>${Number(breakdown.total_score).toFixed(3)}</strong>`;

  row.appendChild(title);
  row.appendChild(score);

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

  li.appendChild(row);
  li.appendChild(meta);
  li.appendChild(scorebarForItem(item));
  li.appendChild(comps);

  li.addEventListener("click", () => selectResult(dest.id));
  li.addEventListener("mouseenter", () => highlightMarker(dest.id));
  li.addEventListener("mouseleave", () => highlightMarker(state.selectedId));

  return li;
}

function computeViewOrder() {
  const search = String(el("result-search").value || "").trim().toLowerCase();
  const tags = parseTagList(el("result-tags").value);
  const loc = String(el("result-location").value || "").trim().toLowerCase();
  const locTokens = loc ? loc.split(/\s+/).filter(Boolean) : [];
  const minScore = Number(el("result-min-score").value || 0) || 0;
  const sort = String(el("result-sort").value || "rank");

  const items = state.baseOrder
    .map((id) => state.resultsById[id])
    .filter(Boolean)
    .filter((it) => {
      const name = String(it.destination.name || "").toLowerCase();
      const district = String(it.destination.district || "").toLowerCase();
      const city = String(it.destination.city || "").toLowerCase();
      const id = String(it.destination.id || "").toLowerCase();
      const score = Number(it.breakdown.total_score) || 0;
      if (score < minScore) return false;
      if (
        search &&
        !(name.includes(search) || district.includes(search) || city.includes(search) || id.includes(search))
      ) {
        return false;
      }
      if (loc) {
        const cityDistrict = `${city} ${district}`.trim();
        const ok =
          city.includes(loc) ||
          district.includes(loc) ||
          cityDistrict.includes(loc) ||
          (locTokens.length > 1 && locTokens.every((t) => city.includes(t) || district.includes(t)));
        if (!ok) return false;
      }
      if (tags.length) {
        const tagSet = new Set((it.destination.tags || []).map((t) => String(t).toLowerCase()));
        if (!tags.every((t) => tagSet.has(t))) return false;
      }
      return true;
    });

  const scoreOf = (it) => Number(it.breakdown.total_score) || 0;
  const nameOf = (it) => String(it.destination.name || "").toLowerCase();
  const stableRank = (it) => state.baseRankById[it.destination.id] || 9999;

  if (sort === "score_desc") items.sort((a, b) => scoreOf(b) - scoreOf(a) || stableRank(a) - stableRank(b));
  if (sort === "score_asc") items.sort((a, b) => scoreOf(a) - scoreOf(b) || stableRank(a) - stableRank(b));
  if (sort === "name_asc") items.sort((a, b) => nameOf(a).localeCompare(nameOf(b)) || stableRank(a) - stableRank(b));
  // sort === "rank": filtered base order is already stable

  return items.map((it) => it.destination.id);
}

function updateView({ selectDefault } = { selectDefault: false }) {
  state.viewOrder = computeViewOrder();
  const resultsEl = el("results");
  resultsEl.innerHTML = "";

  state.viewOrder.forEach((id, idx) => {
    const item = state.resultsById[id];
    if (!item) return;
    resultsEl.appendChild(buildResultListItem(item, { rank: idx + 1 }));
  });

  const count = el("result-count");
  if (count) count.textContent = `${state.viewOrder.length}/${state.baseOrder.length} shown`;

  const selectionVisible = state.selectedId && state.viewOrder.includes(state.selectedId);
  if (selectDefault || (!selectionVisible && state.viewOrder.length)) {
    const first = selectionVisible ? state.selectedId : state.viewOrder[0];
    if (first) selectResult(first, { focusTab: false });
  }
  if (!state.viewOrder.length) {
    el("inspector").innerHTML = `<p class="hint">No results match your filters.</p>`;
    renderDebug();
    updateRouteLineForSelected();
  }

  renderMapFromOrder(state.viewOrder);
}

function openTab(name) {
  state.activeTab = name;

  const tabs = [
    ["results", "tab-results", "panel-results"],
    ["details", "tab-details", "panel-details"],
    ["debug", "tab-debug", "panel-debug"],
  ];

  tabs.forEach(([key, tabId, panelId]) => {
    const tab = el(tabId);
    const panel = el(panelId);
    const active = key === name;
    if (tab) {
      tab.classList.toggle("active", active);
      tab.setAttribute("aria-selected", active ? "true" : "false");
    }
    if (panel) panel.classList.toggle("active", active);
  });
  saveUiState();
}

function renderDebug() {
  const resp = el("debug-response");
  const sel = el("debug-selected");
  const meta = el("debug-meta");
  const tdx = el("debug-tdx");
  if (resp) resp.textContent = JSON.stringify(state.lastResponse || {}, null, 2);
  if (sel) {
    const id = state.selectedId;
    sel.textContent = id && state.resultsById[id] ? JSON.stringify(state.resultsById[id], null, 2) : "{}";
  }

  if (meta) {
    const r = state.lastResponse;
    const cache = r && r.meta && r.meta.cache ? r.meta.cache : null;
    const cacheLine = cache
      ? `Cache: hit ${Number(cache.hits) || 0} · miss ${Number(cache.misses) || 0} · expired ${
          Number(cache.expired) || 0
        } · stale ${Number(cache.stale_fallbacks) || 0}`
      : "Cache: —";
    const gen = r && r.generated_at ? String(r.generated_at) : "—";
    const warns = r && r.meta && Array.isArray(r.meta.warnings) ? r.meta.warnings : [];
    const warnLine = warns.length ? `Warnings: ${warns.length} (see response JSON)` : "Warnings: 0";
    const sources = r && r.meta && r.meta.data_sources ? r.meta.data_sources : null;
    const srcLine = sources
      ? `TDX bus=${sources.tdx ? sources.tdx.bus_stops_count : "—"} · bike=${
          sources.tdx ? sources.tdx.bike_status_count : "—"
        } · metro=${sources.tdx ? sources.tdx.metro_stations_count : "—"} · parking=${
          sources.tdx ? sources.tdx.parking_lots_count : "—"
        }`
      : "Sources: —";
    meta.innerHTML = `<strong>Run summary</strong><br/>Generated at: ${gen}<br/>${cacheLine}<br/>${warnLine}<br/>${srcLine}`;
  }

  if (tdx) {
    const st = state.tdxStatus;
    if (!st || !st.items) {
      tdx.innerHTML = `<strong>TDX prefetch</strong><br/>Status unavailable.`;
    } else {
      const updated = st.last_updated_at_unix ? formatUnix(st.last_updated_at_unix) : "—";
      const lines = (st.items || [])
        .slice(0, 12)
        .map((it) => {
          const mark = it.done ? "done" : "pending";
          const scope = `${it.dataset}:${it.scope}`;
          return `${scope} → ${mark}`;
        })
        .join("<br/>");
      tdx.innerHTML = `<strong>TDX prefetch</strong><br/>City: ${st.city || "—"}<br/>Updated: ${updated}<br/>${lines}`;
    }
  }
}

function renderMapFromOrder(order) {
  const originLat = numberValue("origin-lat");
  const originLon = numberValue("origin-lon");
  if (originLat === null || originLon === null) return;

  const m = ensureMap(originLat, originLon);
  if (!m) return;

  clearMarkers();
  updateOrigin(originLat, originLon, { center: false, source: "manual" });

  const useCluster = Boolean(window.L && window.L.markerClusterGroup);
  const group = useCluster ? L.markerClusterGroup({ showCoverageOnHover: false }) : null;
  state.markerGroup = group;

  order.forEach((id, idx) => {
    const item = state.resultsById[id];
    if (!item) return;
    const d = item.destination;
    const score = Number(item.breakdown.total_score) || 0;
    const marker = L.marker([d.location.lat, d.location.lon], {
      icon: destIcon(idx + 1, false),
      __rank: idx + 1,
    });
    marker.bindPopup(`${idx + 1}. ${d.name}<br/>score ${score.toFixed(3)}`);
    marker.bindTooltip(`${d.name} · ${score.toFixed(3)}`, { direction: "top", opacity: 0.9, sticky: true });
    marker.on("click", () => selectResult(d.id));
    marker.on("mouseover", () => highlightMarker(d.id));
    marker.on("mouseout", () => highlightMarker(state.selectedId));
    state.destMarkers[d.id] = marker;
    if (group) group.addLayer(marker);
    else marker.addTo(m);
  });

  if (group) group.addTo(m);

  highlightMarker(state.selectedId);
  updateRouteLineForSelected();
  updateRouteLinesForAll();
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
    showLines: state.showLines,
    activeTab: state.activeTab,
    activeSetupStep: state.activeSetupStep,
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
    state.showLines = Boolean(ui.showLines);
    state.activeTab = ui.activeTab || "results";
    state.activeSetupStep = ui.activeSetupStep || "step-1";
  } catch (_) {
    // Ignore
  }
}

function openSetupStep(stepId) {
  const valid = new Set(["step-1", "step-2", "step-3", "step-4"]);
  const next = valid.has(stepId) ? stepId : "step-1";
  state.activeSetupStep = next;

  document.querySelectorAll(".step-section").forEach((node) => {
    node.classList.toggle("active", node.id === next);
  });
  document.querySelectorAll(".step-tab").forEach((btn) => {
    const active = btn.dataset.step === next;
    btn.classList.toggle("active", active);
    btn.setAttribute("aria-selected", active ? "true" : "false");
  });
  saveUiState();
}

function updateBriefStrip() {
  const startIso = toTaipeiIso(el("start").value);
  const endIso = toTaipeiIso(el("end").value);
  const when = startIso && endIso ? `${fromTaipeiIso(startIso)} → ${fromTaipeiIso(endIso)}` : "—";
  const lat = numberValue("origin-lat");
  const lon = numberValue("origin-lon");
  const from = lat !== null && lon !== null ? `${Number(lat).toFixed(4)}, ${Number(lon).toFixed(4)}` : "—";

  const preset = el("preset").value || "";
  const goal = preset ? preset : "Custom";
  const topn = numberValue("top-n");

  el("brief-when").textContent = `When: ${when}`;
  el("brief-from").textContent = `From: ${from}`;
  el("brief-goal").textContent = `Goal: ${goal}`;
  el("brief-topn").textContent = `Top N: ${topn || "—"}`;

  const resp = state.lastResponse;
  const errors = resp && resp.results ? countTdxErrors(resp.results) : 0;
  const warnings = resp && resp.meta && Array.isArray(resp.meta.warnings) ? resp.meta.warnings.length : 0;
  const warnSuffix = warnings ? ` · ${warnings} warning${warnings === 1 ? "" : "s"}` : "";
  el("brief-data").textContent = errors ? `Data: Degraded (${errors})${warnSuffix}` : `Data: OK${warnSuffix}`;

  const cache = resp && resp.meta && resp.meta.cache ? resp.meta.cache : null;
  if (cache) {
    const hits = Number(cache.hits) || 0;
    const misses = Number(cache.misses) || 0;
    const stale = Number(cache.stale_fallbacks) || 0;
    el("brief-cache").textContent = `Cache: hit ${hits} · miss ${misses} · stale ${stale}`;
  } else {
    el("brief-cache").textContent = "Cache: —";
  }

  if (state.tdxStatus && state.tdxStatus.last_updated_at_unix) {
    const parts = [];
    parts.push(`bulk ${formatUnix(state.tdxStatus.last_updated_at_unix)}`);

    const daemon = state.tdxStatus.daemon;
    const tdxm = daemon && daemon.tdx_client;
    if (tdxm && tdxm.last_success_unix) parts.push(`tdx ok ${formatUnix(tdxm.last_success_unix)}`);
    if (tdxm && typeof tdxm.requests_per_hour === "number") parts.push(`req/h ${tdxm.requests_per_hour}`);

    const d = daemon && daemon.daemon;
    if (d && d.global_cooldown_until_unix) {
      const now = Math.floor(Date.now() / 1000);
      if (now < Number(d.global_cooldown_until_unix)) parts.push("cooldown");
    }

    el("brief-updated").textContent = `Updated: ${parts.join(" · ")}`;
  } else {
    el("brief-updated").textContent = "Updated: —";
  }
}

function sliderLevelLabel(v) {
  const x = Number(v);
  if (!Number.isFinite(x)) return "—";
  if (x < 0.34) return "Low";
  if (x < 0.67) return "Medium";
  return "High";
}

function bindSlider(id) {
  const input = el(id);
  const valueEl = el(`${id}-value`);
  const labelEl = el(`${id}-label`);
  if (!input || !valueEl || !labelEl) return;

  const update = () => {
    const v = Number(input.value);
    valueEl.textContent = v.toFixed(2);
    labelEl.textContent = sliderLevelLabel(v);
    updateBriefStrip();
  };
  input.addEventListener("input", update);
  sliderUpdaters[id] = update;
  update();
}

const sliderUpdaters = {};
const SLIDER_IDS = [
  "w-accessibility",
  "w-weather",
  "w-preference",
  "w-context",
  "avoid-rain",
  "avoid-crowds",
  "family-importance",
  "tag-indoor",
  "tag-outdoor",
  "tag-culture",
  "tag-food",
  "tag-family",
  "tag-crowd",
];

function refreshSliders() {
  SLIDER_IDS.forEach((id) => {
    const fn = sliderUpdaters[id];
    if (fn) fn();
  });
}

function applyModeBalanced() {
  applySettingsDefaults();
  refreshSliders();
  updateBriefStrip();
}

function applyModeRainyDay() {
  setNumberValue("w-accessibility", 0.25);
  setNumberValue("w-weather", 0.45);
  setNumberValue("w-preference", 0.20);
  setNumberValue("w-context", 0.10);
  setNumberValue("avoid-rain", 0.9);
  setNumberValue("avoid-crowds", 0.55);
  setNumberValue("family-importance", 0.25);
  setNumberValue("tag-indoor", 1.0);
  setNumberValue("tag-outdoor", 0.05);
  setNumberValue("tag-culture", 0.6);
  setNumberValue("tag-food", 0.5);
  setNumberValue("tag-family", 0.25);
  setNumberValue("tag-crowd", 0.55);
  refreshSliders();
  updateBriefStrip();
}

function applyModeFamily() {
  setNumberValue("w-accessibility", 0.35);
  setNumberValue("w-weather", 0.20);
  setNumberValue("w-preference", 0.20);
  setNumberValue("w-context", 0.25);
  setNumberValue("avoid-rain", 0.6);
  setNumberValue("avoid-crowds", 0.75);
  setNumberValue("family-importance", 0.9);
  setNumberValue("tag-family", 1.0);
  setNumberValue("tag-crowd", 0.8);
  setNumberValue("tag-indoor", 0.7);
  setNumberValue("tag-outdoor", 0.3);
  setNumberValue("tag-culture", 0.25);
  setNumberValue("tag-food", 0.25);
  refreshSliders();
  updateBriefStrip();
}

function countTdxErrors(items) {
  let n = 0;
  (items || []).forEach((it) => {
    const comps = (it.breakdown && it.breakdown.components) || [];
    comps.forEach((c) => {
      const errors = c.details && c.details.tdx_errors;
      if (errors && typeof errors === "object") n += Object.keys(errors).length;
    });
  });
  return n;
}

async function refreshTdxStatus() {
  try {
    state.tdxStatus = await fetchJson("/api/tdx/status");
  } catch (_) {
    state.tdxStatus = null;
  } finally {
    updateBriefStrip();
    renderCoverage();
    renderDebug();
  }
}

async function refreshQualityReport() {
  try {
    state.qualityReport = await fetchJson("/api/quality/report");
  } catch (_) {
    state.qualityReport = null;
  } finally {
    renderCoverage();
    renderPolicySummary();
  }
}

function renderCoverage() {
  const summaryNode = el("coverage-summary");
  const tableNode = el("coverage-table");
  if (!summaryNode || !tableNode) return;

  const report = state.qualityReport;
  const cov = report && report.tdx && report.tdx.bulk_coverage;
  if (!cov) {
    summaryNode.textContent = "Coverage report unavailable.";
    tableNode.innerHTML = "";
    return;
  }

  const byDataset = (cov.summary && cov.summary.by_dataset) || {};
  const lastUpdated = cov.last_updated_at_unix;
  const updatedStr = lastUpdated ? new Date(lastUpdated * 1000).toLocaleString() : "—";

  const worst = (report && report.overall && report.overall.severity) || "info";
  const worstBadge =
    worst === "error"
      ? `<span class="badge bad">error</span>`
      : worst === "warning"
      ? `<span class="badge warn">warning</span>`
      : `<span class="badge ok">info</span>`;

  summaryNode.innerHTML = `Overall: ${worstBadge} · Last updated: <span class="badge">${escapeHtml(updatedStr)}</span>`;

  const rows = Object.entries(byDataset).map(([dataset, stats]) => {
    const s = stats || {};
    return {
      dataset,
      done: Number(s.done || 0),
      unsupported: Number(s.unsupported || 0),
      error429: Number(s.error_429 || 0),
      errorOther: Number(s.error_other || 0),
      incomplete: Number(s.incomplete || 0),
      missing: Number(s.missing || 0),
    };
  });
  rows.sort((a, b) => a.dataset.localeCompare(b.dataset));

  const th = (t) => `<th>${escapeHtml(t)}</th>`;
  const td = (t) => `<td>${t}</td>`;
  const badge = (n, cls) => `<span class="badge ${cls || ""}">${escapeHtml(String(n))}</span>`;

  const html = [];
  html.push("<table>");
  html.push("<thead><tr>");
  html.push(th("Dataset"));
  html.push(th("Done"));
  html.push(th("Unsupported (404)"));
  html.push(th("429"));
  html.push(th("Other errors"));
  html.push(th("Incomplete"));
  html.push(th("Missing"));
  html.push("</tr></thead>");
  html.push("<tbody>");
  rows.forEach((r) => {
    const warnish = r.error429 + r.errorOther + r.incomplete + r.missing > 0;
    html.push("<tr>");
    html.push(td(`<strong>${escapeHtml(r.dataset)}</strong>`));
    html.push(td(badge(r.done, "ok")));
    html.push(td(badge(r.unsupported, "warn")));
    html.push(td(badge(r.error429, r.error429 > 0 ? "bad" : "")));
    html.push(td(badge(r.errorOther, r.errorOther > 0 ? "bad" : "")));
    html.push(td(badge(r.incomplete, warnish ? "warn" : "")));
    html.push(td(badge(r.missing, warnish ? "warn" : "")));
    html.push("</tr>");
  });
  html.push("</tbody></table>");
  tableNode.innerHTML = html.join("");
}

async function loadCatalogMeta() {
  try {
    state.catalogMeta = await fetchJson("/api/catalog/meta");
  } catch (_) {
    state.catalogMeta = null;
    return;
  }

  const tags = el("catalog-tags");
  if (tags && state.catalogMeta && Array.isArray(state.catalogMeta.tags)) {
    tags.innerHTML = "";
    state.catalogMeta.tags.forEach((t) => {
      const opt = document.createElement("option");
      opt.value = t;
      tags.appendChild(opt);
    });
  }

  const locs = el("catalog-locations");
  if (locs && state.catalogMeta) {
    const options = new Set();
    (state.catalogMeta.cities || []).forEach((c) => options.add(String(c)));
    const byCity = state.catalogMeta.districts_by_city || {};
    Object.entries(byCity).forEach(([city, districts]) => {
      (districts || []).forEach((d) => {
        options.add(String(d));
        options.add(`${city} ${d}`);
      });
    });
    locs.innerHTML = "";
    [...options].sort().forEach((v) => {
      const opt = document.createElement("option");
      opt.value = v;
      locs.appendChild(opt);
    });
  }
}

function loadTdxJobId() {
  try {
    state.tdxJobId = localStorage.getItem(STORAGE.tdxJobId);
  } catch (_) {
    state.tdxJobId = null;
  }
}

function saveTdxJobId(jobId) {
  state.tdxJobId = jobId || null;
  try {
    if (state.tdxJobId) localStorage.setItem(STORAGE.tdxJobId, state.tdxJobId);
    else localStorage.removeItem(STORAGE.tdxJobId);
  } catch (_) {
    // ignore
  }
}

function setTdxJobStatus(text) {
  const node = el("tdx-job-status");
  if (node) node.textContent = text || "";
}

async function refreshTdxJob() {
  if (!state.tdxJobId) {
    state.tdxJob = null;
    setTdxJobStatus("No running job.");
    return;
  }
  try {
    state.tdxJob = await fetchJson(`/api/tdx/prefetch/${encodeURIComponent(state.tdxJobId)}`);
    const j = state.tdxJob;
    const p = j.progress;
    const prog = p ? `${p.done_count}/${p.total_count} done` : "—";
    const err = j.last_error ? ` · last error: ${j.last_error.type}` : "";
    setTdxJobStatus(`Job ${j.job_id} · ${j.status} · city=${j.city} · ${prog}${err}`);
    if (j.status === "completed" || j.status === "canceled") {
      // Keep job id for history, but stop polling.
      return;
    }
    setTimeout(refreshTdxJob, 4000);
  } catch (e) {
    setTdxJobStatus(`Job status unavailable: ${e.message}`);
  }
}

async function startTdxJobFromUi() {
  const city = String(el("tdx-city").value || "").trim();
  const sleepSeconds = Number(el("tdx-sleep").value || 2) || 0;
  const datasetsPerRun = Number(el("tdx-datasets-per-run").value || 0) || 0;
  const reset = Boolean(el("tdx-reset").checked);

  setTdxJobStatus("Starting…");
  try {
    const resp = await fetch("/api/tdx/prefetch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        city: city || null,
        datasets: [
          "bus_stops",
          "bus_routes",
          "bike_stations",
          "bike_availability",
          "metro_stations",
          "parking_lots",
          "parking_availability",
        ],
        reset,
        sleep_seconds: sleepSeconds,
        datasets_per_run: datasetsPerRun,
      }),
    });
    if (!resp.ok) {
      const payload = await resp.json().catch(() => ({}));
      throw new Error(apiErrorMessage({ status: resp.status, detail: payload.detail || payload }));
    }
    const data = await resp.json();
    saveTdxJobId(data.job_id);
    await refreshTdxJob();
  } catch (e) {
    setTdxJobStatus(`Start failed: ${e.message}`);
  }
}

async function cancelTdxJob() {
  if (!state.tdxJobId) {
    setTdxJobStatus("No job to cancel.");
    return;
  }
  setTdxJobStatus("Canceling…");
  try {
    const resp = await fetch(`/api/tdx/prefetch/${encodeURIComponent(state.tdxJobId)}/cancel`, {
      method: "POST",
    });
    if (!resp.ok) {
      const payload = await resp.json().catch(() => ({}));
      throw new Error(apiErrorMessage({ status: resp.status, detail: payload.detail || payload }));
    }
    await resp.json().catch(() => ({}));
    await refreshTdxJob();
  } catch (e) {
    setTdxJobStatus(`Cancel failed: ${e.message}`);
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
    refreshSliders();
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
  const version = preset && preset.version ? ` (v${preset.version})` : "";
  node.value = preset && preset.description ? `${preset.description}${version}` : `${name}${version}`;
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

  refreshSliders();
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
  refreshSliders();
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
  const started = performance.now();
  setBusy(true, `${statusPrefix}Requesting recommendations…`);

  let payload;
  try {
    payload = buildPreferencesPayload();
  } catch (e) {
    setBusy(false, `Error: ${e.message}`);
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
      throw new Error(apiErrorMessage({ status: resp.status, detail: err.detail || err }));
    }
    const data = await resp.json();
    const ms = Math.round(performance.now() - started);
    setBusy(false, `${statusPrefix}Got ${data.results.length} results in ${ms}ms.`);
    openTab("results");
    setResultsPayload(data);
    renderDebug();
    updateBriefStrip();
    refreshTdxStatus();
  } catch (e) {
    setBusy(false, `${statusPrefix}Error: ${e.message}`);
  }
}

function onPresetChanged() {
  if (state.applyingPreset) return;
  const name = el("preset").value || "";
  setPresetDescription(name);
  updateBriefStrip();
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
  refreshSliders();

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
    saved_at_unix: Math.floor(Date.now() / 1000),
    based_on_preset: payload.preset || null,
    based_on_version:
      payload.preset && state.serverPresets[payload.preset] ? state.serverPresets[payload.preset].version || null : null,
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

  if (/^[1-9]$/.test(evt.key) && state.viewOrder.length) {
    const idx = Number(evt.key) - 1;
    const id = state.viewOrder[idx];
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
    el("debug-response").textContent = "{}";
    el("debug-selected").textContent = "{}";
    el("result-count").textContent = "";
    state.lastResponse = null;
    state.resultsById = {};
    state.baseOrder = [];
    state.viewOrder = [];
    state.baseRankById = {};
    state.selectedId = null;
    setStatus("Cleared results and markers.");
  });

  el("fit-btn").addEventListener("click", () => fitToResults());
  el("show-lines").addEventListener("change", (e) => {
    state.showLines = Boolean(e.target.checked);
    saveUiState();
    updateRouteLinesForAll();
  });

  el("mode-balanced").addEventListener("click", () => {
    applyModeBalanced();
    if (state.autoRun) scheduleAutoRun("mode:balanced");
  });
  el("mode-rainy").addEventListener("click", () => {
    applyModeRainyDay();
    if (state.autoRun) scheduleAutoRun("mode:rainy");
  });
  el("mode-family").addEventListener("click", () => {
    applyModeFamily();
    if (state.autoRun) scheduleAutoRun("mode:family");
  });

  document.querySelectorAll(".step-tab").forEach((btn) => {
    btn.addEventListener("click", () => openSetupStep(btn.dataset.step));
  });
  document.querySelectorAll("[data-next-step]").forEach((btn) => {
    btn.addEventListener("click", () => openSetupStep(btn.dataset.nextStep));
  });
  document.querySelectorAll("[data-prev-step]").forEach((btn) => {
    btn.addEventListener("click", () => openSetupStep(btn.dataset.prevStep));
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
    updateBriefStrip();
    if (state.autoRun) scheduleAutoRun("time_window");
  });
  el("start").addEventListener("change", () => {
    applyQuickWindow();
    updateBriefStrip();
    if (state.autoRun) scheduleAutoRun("time_window");
  });
  el("end").addEventListener("change", () => {
    updateBriefStrip();
    if (state.autoRun) scheduleAutoRun("time_window");
  });

  el("origin-lat").addEventListener("change", () => {
    const lat = numberValue("origin-lat");
    const lon = numberValue("origin-lon");
    if (lat !== null && lon !== null) updateOrigin(lat, lon, { center: false, source: "manual" });
    updateBriefStrip();
  });
  el("origin-lon").addEventListener("change", () => {
    const lat = numberValue("origin-lat");
    const lon = numberValue("origin-lon");
    if (lat !== null && lon !== null) updateOrigin(lat, lon, { center: false, source: "manual" });
    updateBriefStrip();
  });

  ["result-search", "result-tags", "result-location"].forEach((id) => {
    el(id).addEventListener("input", () => updateView({ selectDefault: false }));
  });
  ["result-sort", "result-min-score"].forEach((id) => {
    el(id).addEventListener("change", () => updateView({ selectDefault: false }));
  });

  el("tab-results").addEventListener("click", () => openTab("results"));
  el("tab-details").addEventListener("click", () => openTab("details"));
  el("tab-debug").addEventListener("click", () => {
    renderDebug();
    openTab("debug");
  });

  el("tdx-start-btn").addEventListener("click", () => startTdxJobFromUi());
  el("tdx-cancel-btn").addEventListener("click", () => cancelTdxJob());

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
  loadTdxJobId();
  el("auto-run").checked = state.autoRun;
  el("pick-origin").checked = state.pickOrigin;
  el("enable-overrides").checked = state.overridesEnabled;
  setAdvancedOverridesEnabled(state.overridesEnabled);
  el("keyboard-controls").checked = state.keyboardControls;
  el("move-step").value = String(state.moveStepM || 50);
  setNumberValue("heading-deg", state.headingDeg || 0);

  await loadServerSettings();
  await loadServerPresets();
  await loadCatalogMeta();
  loadCustomPresets();
  rebuildPresetSelect();

  applySettingsDefaults();

  const saved = loadLastQuery();
  loadSavedQueryIntoForm(saved);
  setDefaultTimes();

  setPresetDescription(el("preset").value || "");
  el("delete-preset-btn").disabled = !Boolean(state.customPresets[el("preset").value || ""]);

  if (state.settings && state.settings.ingestion && state.settings.ingestion.tdx) {
    const city = state.settings.ingestion.tdx.city;
    if (el("tdx-city") && !el("tdx-city").value) el("tdx-city").value = city || "";
  }

  const lat = numberValue("origin-lat") || 25.0478;
  const lon = numberValue("origin-lon") || 121.517;
  ensureMap(lat, lon);
  updateOrigin(lat, lon, { center: true, source: "manual" });

  initDom();
  el("show-lines").checked = state.showLines;
  openTab(state.activeTab || "results");
  openSetupStep(state.activeSetupStep || "step-1");
  updateBriefStrip();
  await refreshQualityReport();
  await refreshTdxStatus();
  await refreshTdxJob();
  SLIDER_IDS.forEach(bindSlider);
  setStatus("Ready. Press Ctrl+Enter to run.");

  setInterval(refreshTdxStatus, 30_000);
  setInterval(refreshQualityReport, 60_000);
})();

function updateRouteLineForSelected() {
  if (!state.map) return;
  const originLat = numberValue("origin-lat");
  const originLon = numberValue("origin-lon");
  if (originLat === null || originLon === null) return;

  const id = state.selectedId;
  const item = id ? state.resultsById[id] : null;
  if (!item) {
    if (state.routeLine) {
      state.routeLine.remove();
      state.routeLine = null;
    }
    return;
  }

  const to = [item.destination.location.lat, item.destination.location.lon];
  const from = [originLat, originLon];
  const points = [from, to];
  if (!state.routeLine) {
    state.routeLine = L.polyline(points, { color: "#1f9ad6", weight: 3, opacity: 0.9 }).addTo(state.map);
  } else {
    state.routeLine.setLatLngs(points);
  }

  const d = haversineMeters(originLat, originLon, item.destination.location.lat, item.destination.location.lon);
  const text = `Distance ${formatMeters(d)}`;
  if (!state.routeLine.getTooltip()) {
    state.routeLine.bindTooltip(text, { permanent: true, direction: "center", className: "route-label" });
  } else {
    state.routeLine.setTooltipContent(text);
  }
}

function updateRouteLinesForAll() {
  if (!state.map) return;
  if (state.routeLines && state.routeLines.length) {
    state.routeLines.forEach((l) => l.remove());
    state.routeLines = [];
  }
  if (!state.showLines) return;

  const originLat = numberValue("origin-lat");
  const originLon = numberValue("origin-lon");
  if (originLat === null || originLon === null) return;
  const from = [originLat, originLon];

  state.viewOrder.slice(0, 30).forEach((id) => {
    const it = state.resultsById[id];
    if (!it) return;
    const to = [it.destination.location.lat, it.destination.location.lon];
    const line = L.polyline([from, to], { color: "#6aa9ff", weight: 2, opacity: 0.25 }).addTo(state.map);
    state.routeLines.push(line);
  });
}

function fitToResults() {
  if (!state.map) return;
  const originLat = numberValue("origin-lat");
  const originLon = numberValue("origin-lon");
  if (originLat === null || originLon === null) return;

  const pts = [[originLat, originLon]];
  state.viewOrder.forEach((id) => {
    const it = state.resultsById[id];
    if (!it) return;
    pts.push([it.destination.location.lat, it.destination.location.lon]);
  });
  if (pts.length < 2) {
    state.map.setView([originLat, originLon], Math.max(state.map.getZoom(), 12));
    return;
  }
  const bounds = L.latLngBounds(pts);
  state.map.fitBounds(bounds.pad(0.12));
}
