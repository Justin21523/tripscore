export function validatePreferencesPayload(payload) {
  if (!payload || typeof payload !== "object") throw new Error("Invalid request payload.");
  const o = payload.origin || {};
  if (o.lat === null || o.lat === undefined || o.lon === null || o.lon === undefined) {
    throw new Error("Origin is required (lat/lon).");
  }
  if (!Number.isFinite(Number(o.lat)) || !Number.isFinite(Number(o.lon))) {
    throw new Error("Origin is required (lat/lon).");
  }
  const tw = payload.time_window || {};
  if (!tw.start || !tw.end) {
    throw new Error("Time window is required (start/end).");
  }
  const start = new Date(String(tw.start));
  const end = new Date(String(tw.end));
  if (!Number.isFinite(start.getTime()) || !Number.isFinite(end.getTime())) {
    throw new Error("Invalid time format. Please re-select start/end.");
  }
  if (end.getTime() <= start.getTime()) {
    throw new Error("End time must be after start time.");
  }
  const n = payload.max_results;
  if (n !== null && n !== undefined && (!Number.isFinite(Number(n)) || Number(n) < 1)) {
    throw new Error("Top N must be at least 1.");
  }
}
