const STORAGE_KEY = "tripscore.theme.v1";

function loadTheme() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? String(raw) : "";
  } catch (_) {
    return "";
  }
}

function saveTheme(theme) {
  try {
    if (theme) localStorage.setItem(STORAGE_KEY, String(theme));
    else localStorage.removeItem(STORAGE_KEY);
  } catch (_) {
    // ignore
  }
}

function applyTheme(theme) {
  const t = String(theme || "").trim();
  const html = document.documentElement;
  if (!t || t === "light") {
    html.removeAttribute("data-theme");
    return;
  }
  html.setAttribute("data-theme", t);
}

export function initThemeControls({ showToast } = { showToast: null }) {
  const select = document.getElementById("theme-select");
  if (!select) return;
  const cur = loadTheme() || "light";
  select.value = cur;
  applyTheme(cur);
  if (select.dataset.bound === "1") return;
  select.dataset.bound = "1";
  select.addEventListener("change", () => {
    const v = String(select.value || "light");
    saveTheme(v);
    applyTheme(v);
    if (typeof showToast === "function") showToast("Theme", `Theme set to ${v}.`, { kind: "ok", timeoutMs: 2000 });
  });
}

