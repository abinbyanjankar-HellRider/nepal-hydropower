// Shared helpers. All data comes from scraped sources, so DOM is built with textContent only.
const STATUS_COLORS = {
  "Operational": "#1a9e6b", "Under Construction": "#e08a1e", "Licensed": "#5b6bd6",
  "Planned": "#8a94a6", "Suspended": "#c2453d", "Decommissioned": "#5a5f6a",
};

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

const fmt = (n, d = 0) => (n === null || n === undefined) ? "–" :
  Number(n).toLocaleString(undefined, { maximumFractionDigits: d, minimumFractionDigits: d });

function statusPill(status) {
  return el("span", { class: "pill " + status.replace(/ /g, "-") }, status);
}

function projectLink(id, name) { return el("a", { href: "/projects/" + encodeURIComponent(id) }, name); }
function companyLink(id, name) { return el("a", { href: "/companies/" + encodeURIComponent(id) }, name); }

// columns: [{key, label, num?, render?(row) -> Node|string, sort?}]
function renderTable(container, columns, rows, opts = {}) {
  container.replaceChildren();
  if (!rows.length) { container.append(el("div", { class: "empty" }, opts.empty || "No rows.")); return; }
  const head = el("tr", {}, columns.map(c => {
    const th = el("th", { class: (c.num ? "num " : "") + (c.sort ? "sortable" : "") }, c.label);
    if (c.sort && opts.onSort) th.addEventListener("click", () => opts.onSort(c.sort));
    if (c.sort && opts.sortKey === c.sort) th.append(opts.sortDir === "asc" ? " ▲" : " ▼");
    return th;
  }));
  const body = rows.map(r => el("tr", {}, columns.map(c =>
    el("td", { class: c.num ? "num" : "" }, c.render ? c.render(r) : (r[c.key] ?? "–")))));
  container.append(el("div", { class: "table-wrap" }, el("table", { class: "data" }, el("thead", {}, head), el("tbody", {}, body))));
}

const chartFont = getComputedStyle(document.body).color;
function baseChartOptions(extra) {
  if (window.Chart) { Chart.defaults.color = getComputedStyle(document.body).getPropertyValue("--muted").trim() || "#667085";
                      Chart.defaults.borderColor = getComputedStyle(document.body).getPropertyValue("--line").trim() || "#e3e7ee"; }
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
