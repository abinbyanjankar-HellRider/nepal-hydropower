// Shared helpers. All data comes from scraped sources, so DOM is built with textContent only.
const ICONS = "/static/vendor/icons.svg";
const cssVar = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

// Status colours live in style.css (--st-*) so light and dark themes stay in one place; read them live.
const STATUS_VARS = {
  "Operational": "--st-operational", "Under Construction": "--st-construction", "Licensed": "--st-licensed",
  "Planned": "--st-planned", "Suspended": "--st-suspended", "Decommissioned": "--st-decommissioned",
};
const STATUS_COLORS = {};
Object.entries(STATUS_VARS).forEach(([k, v]) => Object.defineProperty(STATUS_COLORS, k, { get: () => cssVar(v), enumerable: true }));
const CHART_SERIES = () => ["--chart-1", "--chart-2", "--chart-3", "--chart-4", "--chart-5"].map(cssVar);

async function api(path, params) {
  const url = new URL(path, window.location.origin);
  Object.entries(params || {}).forEach(([k, v]) => {
    (Array.isArray(v) ? v : [v]).forEach(x => { if (x !== undefined && x !== null && x !== "") url.searchParams.append(k, x); });
  });
  const res = await fetch(url);
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || res.statusText);
  return res.json();
}

function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") node.className = v;
    else if (k === "onclick") node.addEventListener("click", v);
    else if (v !== null && v !== undefined) node.setAttribute(k, v);
  }
  children.flat().forEach(c => { if (c !== null && c !== undefined) node.append(c instanceof Node ? c : document.createTextNode(String(c))); });
  return node;
}

// Decorative icon from the local Lucide sprite (hidden from screen readers; pair it with visible text).
function icon(name, cls) {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("class", "icon" + (cls ? " " + cls : ""));
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS(ns, "use");
  use.setAttribute("href", ICONS + "#i-" + name);
  svg.append(use);
  return svg;
}

const fmt = (n, d = 0) => (n === null || n === undefined) ? "–" :
  Number(n).toLocaleString(undefined, { maximumFractionDigits: d, minimumFractionDigits: d });

// +12.3% / -4.0% with a sign, so the meaning never depends on the green/red colour alone.
function signedPct(v, title) {
  if (v === null || v === undefined) return "–";
  return el("span", { class: v >= 0 ? "pos" : "neg", title: title || null }, (v >= 0 ? "+" : "") + fmt(v, 1) + "%");
}

function statusPill(status) {
  return el("span", { class: "pill " + status.replace(/ /g, "-") }, status);
}

function projectLink(id, name) { return el("a", { href: "/projects/" + encodeURIComponent(id) }, name); }
function companyLink(id, name) { return el("a", { href: "/companies/" + encodeURIComponent(id) }, name); }

// columns: [{key, label, num?, render?(row) -> Node|string, sort?}]
// Sortable headers are real buttons (keyboard reachable) and expose the current order through aria-sort.
function renderTable(container, columns, rows, opts = {}) {
  container.replaceChildren();
  if (!rows.length) { container.append(el("div", { class: "empty" }, opts.empty || "No rows.")); return; }
  const head = el("tr", {}, columns.map(c => {
    const active = c.sort && opts.sortKey === c.sort;
    const th = el("th", { class: c.num ? "num" : "", scope: "col",
                          "aria-sort": active ? (opts.sortDir === "asc" ? "ascending" : "descending") : null });
    if (c.sort && opts.onSort) {
      const btn = el("button", { type: "button", class: "sort-btn", title: "Sort by " + c.label }, c.label,
                     icon(active ? (opts.sortDir === "asc" ? "arrow-up" : "arrow-down") : "arrow-up-down"));
      btn.addEventListener("click", () => opts.onSort(c.sort));
      th.append(btn);
    } else th.append(c.label);
    return th;
  }));
  const body = rows.map(r => el("tr", {}, columns.map(c =>
    el("td", { class: c.num ? "num" : "" }, c.render ? c.render(r) : (r[c.key] ?? "–")))));
  container.append(el("div", { class: "table-wrap" }, el("table", { class: "data" }, el("thead", {}, head), el("tbody", {}, body))));
}

// Chart.js styling from the theme tokens: subtle grid, muted ticks, legible tooltips, no entrance animation
// when the user asks for reduced motion.
function applyChartTheme() {
  if (!window.Chart) return;
  const d = Chart.defaults;
  d.font.family = cssVar("--font-sans").replace(/"/g, "") || "system-ui";
  d.font.size = 12;
  d.color = cssVar("--muted");
  d.borderColor = cssVar("--grid");
  d.plugins.tooltip.backgroundColor = cssVar("--text");
  d.plugins.tooltip.titleColor = cssVar("--surface");
  d.plugins.tooltip.bodyColor = cssVar("--surface");
  d.plugins.tooltip.padding = 10;
  d.plugins.tooltip.cornerRadius = 6;
  d.plugins.legend.labels.usePointStyle = true;
  d.plugins.legend.labels.boxWidth = 8;
  d.elements.bar.borderRadius = 3;
  d.elements.arc.borderColor = cssVar("--surface");
  if (matchMedia("(prefers-reduced-motion: reduce)").matches) d.animation = false;
}
function baseChartOptions(extra) {
  applyChartTheme();
  return Object.assign({ responsive: true, maintainAspectRatio: false }, extra || {});
}

// NPR amounts: 1,234,000,000 -> "1.23 bn"; 312,905,000 -> "312.9 m"
function npr(v) {
  if (v === null || v === undefined) return "–";
  const a = Math.abs(v);
  if (a >= 1e9) return fmt(v / 1e9, 2) + " bn";
  if (a >= 1e6) return fmt(v / 1e6, 1) + " m";
  return fmt(v, 0);
}

function showError(container, err) {
  container.replaceChildren(el("div", { class: "empty" }, "Could not load data: " + err.message));
}

// ---------- theme toggle ----------
// Charts keep the colours they were drawn with, so on a switch every token value used by a chart
// is swapped for its counterpart in the new theme and the charts are redrawn in place.
const THEME_TOKENS = [...Object.values(STATUS_VARS), "--chart-1", "--chart-2", "--chart-3", "--chart-4", "--chart-5", "--accent", "--accent-strong"];
function isDark() { return getComputedStyle(document.documentElement).colorScheme === "dark"; }
function setTheme(theme) {
  const before = Object.fromEntries(THEME_TOKENS.map(t => [t, cssVar(t)]));
  document.documentElement.dataset.theme = theme;
  try { localStorage.setItem("theme", theme); } catch (e) { /* storage blocked: theme lasts for this page only */ }
  const swap = new Map(THEME_TOKENS.map(t => [before[t].toLowerCase(), cssVar(t)]));
  const remap = v => typeof v === "string" && swap.has(v.toLowerCase()) ? swap.get(v.toLowerCase()) : v;
  if (window.Chart) {
    applyChartTheme();
    Object.values(Chart.instances).forEach(ch => {
      ch.data.datasets.forEach(ds => ["backgroundColor", "borderColor"].forEach(k => {
        if (Array.isArray(ds[k])) ds[k] = ds[k].map(remap); else ds[k] = remap(ds[k]);
      }));
      ch.update("none");
    });
  }
  updateThemeButton();
}
function updateThemeButton() {
  const btn = document.getElementById("theme-toggle");
  if (!btn) return;
  const dark = isDark();
  btn.querySelector(".theme-label").textContent = dark ? "Dark theme" : "Light theme";
  btn.setAttribute("aria-label", dark ? "Switch to light theme" : "Switch to dark theme");
}

// ---------- mobile menu ----------
document.addEventListener("DOMContentLoaded", () => {
  updateThemeButton();
  document.getElementById("theme-toggle")?.addEventListener("click", () => setTheme(isDark() ? "light" : "dark"));
  matchMedia("(prefers-color-scheme: dark)").addEventListener?.("change", updateThemeButton);

  const sidebar = document.getElementById("sidebar"), menuBtn = document.getElementById("menu-btn");
  if (!sidebar || !menuBtn) return;
  const setOpen = open => {
    sidebar.classList.toggle("open", open);
    menuBtn.setAttribute("aria-expanded", String(open));
    menuBtn.setAttribute("aria-label", open ? "Close menu" : "Open menu");
    menuBtn.querySelector("use").setAttribute("href", ICONS + (open ? "#i-x" : "#i-menu"));
  };
  menuBtn.addEventListener("click", () => setOpen(!sidebar.classList.contains("open")));
  document.addEventListener("keydown", e => {
    if (e.key === "Escape" && sidebar.classList.contains("open")) { setOpen(false); menuBtn.focus(); }
  });
  document.addEventListener("click", e => { if (sidebar.classList.contains("open") && !sidebar.contains(e.target)) setOpen(false); });
});
