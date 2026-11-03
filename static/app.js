/* BlockRadar front-end. Plain JS, no build step. */
(() => {
"use strict";

const IST = 19800;                       // chart axis shows IST
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (v, d = 2) => (v == null || isNaN(v)) ? "—" : Number(v).toLocaleString("en-IN", { minimumFractionDigits: d, maximumFractionDigits: d });
const pct = v => (v == null ? "—" : (v > 0 ? "+" : "") + fmt(v) + "%");
const cls = v => v > 0 ? "up" : v < 0 ? "down" : "";

async function api(path, opts = {}) {
  const r = await fetch(path, { ...opts, headers: { "Content-Type": "application/json", "X-BlockRadar": "1", ...(opts.headers || {}) } });
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch (_) {}
    throw new Error(msg);
  }
  return r.json();
}

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg; t.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => (t.hidden = true), 2600);
}

const store = {
  get(k, d) { try { const v = localStorage.getItem("br." + k); return v === null ? d : JSON.parse(v); } catch (_) { return d; } },
  set(k, v) { try { localStorage.setItem("br." + k, JSON.stringify(v)); } catch (_) {} },
};

const state = {
  view: "board", sym: null, sessions: 2, side: "bullish", sector: "", tf: "5",
  status: null, lastUpdated: 0, board: null, prefill: null, symbols: [],
  lastAlert: null, alerts: [], unread: 0, expiry: "",
  sound: store.get("sound", true), desktop: store.get("desktop", false),
};

/* ================================================================ router */
function route() {
  const [view, arg] = (location.hash.slice(1) || "board").split("/");
  state.view = ["board", "chart", "xray", "paper", "journal", "settings"].includes(view) ? view : "board";
  if (state.view === "chart" && arg) state.sym = decodeURIComponent(arg).toUpperCase();
  $$(".view").forEach(v => (v.hidden = v.id !== "view-" + state.view));
  $$("#tabs a").forEach(a => a.classList.toggle("on", a.dataset.view === state.view));
  loadView();
}
window.addEventListener("hashchange", route);

function loadView() {
  if (!state.status || !state.status.ready) {
    if (state.view === "journal") return loadJournal();
    if (state.view === "paper") return loadPaper();
    if (state.view === "settings") return loadSettings();
    return;
  }
  ({ board: loadBoard, chart: loadChart, xray: loadXray, paper: loadPaper, journal: loadJournal, settings: loadSettings })[state.view]();
}

/* ================================================================ status */
async function pollStatus() {
  try {
    const s = await api("/api/status");
    const wasReady = state.status && state.status.ready;
    state.status = s;
    renderStatus(s);
    maybeShowPicker(s);
    if (s.ready && (s.updated !== state.lastUpdated || !wasReady)) {
      state.lastUpdated = s.updated;
      if (["board", "chart", "xray", "paper"].includes(state.view)) loadView();
    }
    if (state.lastAlert === null) state.lastAlert = s.last_alert;
    else if (s.last_alert > state.lastAlert) fetchAlerts();
  } catch (e) {
    $("#status").className = "status err";
    $("#status-text").textContent = "App server not reachable";
  }
  setTimeout(pollStatus, state.status && state.status.mode === "demo" ? 2000 : 5000);
}

function renderStatus(s) {
  const m = s.market || {};
  const el = $("#status");
  el.className = "status " + (s.error ? "err" : s.needs_login ? "warn" : (s.ready && m.open) ? "live" : "warn");
  const parts = [s.mode === "demo" ? "Demo" : (s.broker_name || s.mode)];
  if (m.clock) parts.push(m.clock + " IST");
  if (s.updated_label) parts.push("updated " + s.updated_label);
  $("#status-text").textContent = parts.join(" · ");

  const b = $("#banner");
  let html = "";
  if (s.error) html = esc(s.error);
  else if (s.needs_login) html = !s.has_keys
    ? `Add your ${esc(s.broker_name)} API keys in <a href="#settings">Settings</a> to load live data, or switch to demo mode.`
    : s.broker_auth === "oauth"
      ? `${esc(s.broker_name)} login needed. <a href="/auth/${esc(s.broker)}/login">Connect ${esc(s.broker_name)}</a> (logins expire every morning).`
      : `${esc(s.broker_name)} token expired. Paste a new access token in <a href="#settings">Settings</a>.`;
  else if (!s.ready) {
    const [d, t] = s.progress || [0, 0];
    const w = t ? Math.round(d / t * 100) : 5;
    html = `${esc(s.message || "Loading…")} ${t > 1 ? `(${d}/${t})` : ""}<div class="bar"><i style="width:${w}%"></i></div>`;
  } else if (s.mode === "demo") html = `Demo mode: prices are simulated so you can try every screen. Connect your broker in <a href="#settings">Settings</a>.`;
  else if (!m.open) html = esc(m.label);
  b.innerHTML = html;
  b.hidden = !html;
}

/* ================================================================ board */
async function loadBoard() {
  const data = await api("/api/board?limit=250");
  state.board = data;
  $("#idx-strip").innerHTML = (data.indices || []).map(r => `
    <div class="idx ${r.bias}" data-sym="${esc(r.symbol)}">
      <div class="idx-top"><b>${esc(r.name || r.symbol)}</b><span class="mono">${fmt(r.ltp)}</span><span class="mono ${cls(r.change_pct)}">${pct(r.change_pct)}</span></div>
      <div class="idx-bot"><span class="bias-pill ${r.bias}">${r.bias}${r.score ? " " + r.score : ""}</span>${mtfTags(r)}
        <span class="muted">${r.zone ? `OB <b class="mono gold">${fmt(r.zone.bottom)}–${fmt(r.zone.top)}</b>` : `S <b class="mono">${fmt(r.support)}</b> · R <b class="mono">${fmt(r.resistance)}</b>`}</span>
        ${r.oi ? `<span class="muted">${esc(r.oi.label)}</span>` : ""}</div>
    </div>`).join("");
  const c = data.counts;
  $("#counts").innerHTML =
    `<span><b class="up">${c.bullish}</b>bullish</span><span><b class="down">${c.bearish}</b>bearish</span><span><b>${c.choppy}</b>choppy (filtered out)</span>`;
  const secs = [...new Set([...data.bullish, ...data.bearish].map(r => r.sector))].sort();
  const sel = $("#sector-filter");
  const cur = sel.value;
  sel.innerHTML = `<option value="">All sectors</option>` + secs.map(s => `<option ${s === cur ? "selected" : ""}>${esc(s)}</option>`).join("");
  const PAGE = 20;
  state.page = state.page || { bullish: 0, bearish: 0 };
  for (const side of ["bullish", "bearish"]) {
    const all = data[side].filter(r => !state.sector || r.sector === state.sector);
    const pages = Math.max(1, Math.ceil(all.length / PAGE));
    if (state.page[side] >= pages) state.page[side] = pages - 1;
    const p = state.page[side], start = p * PAGE;
    const rows = all.slice(start, start + PAGE);
    $("#rows-" + side).innerHTML = rows.length ? rows.map((r, i) => rowHtml(r, start + i, side)).join("")
      : `<li class="empty">No ${side} setups${state.sector ? " in " + esc(state.sector) : ""} right now.</li>`;
    let pg = $(`.pager[data-side="${side}"]`);
    if (!pg) {
      pg = document.createElement("div");
      pg.className = "pager";
      pg.dataset.side = side;
      $("#rows-" + side).after(pg);
    }
    pg.innerHTML = all.length > PAGE
      ? `<button class="btn sm" data-dir="-1" ${p === 0 ? "disabled" : ""}>← Previous</button>
         <span>Page <b>${p + 1}</b> of ${pages} · showing ${start + 1}–${start + rows.length} of ${all.length}</span>
         <button class="btn sm" data-dir="1" ${p >= pages - 1 ? "disabled" : ""}>Next →</button>`
      : (all.length ? `<span>All ${all.length} shown</span>` : "");
  }
  $$(".board .col").forEach(col => col.classList.toggle("show", col.dataset.side === state.side));
}

function rowHtml(r, i, side) {
  const tags = [];
  if (r.in_zone) tags.push(`<span class="tag gold">In OB zone</span>`);
  if (r.trap) tags.push(`<span class="tag ${r.trap.dir > 0 ? "g" : "r"}">Liquidity trap</span>`);
  if (r.oi) {
    const good = ["Long buildup", "Short covering"].includes(r.oi.label);
    const bad = ["Short buildup", "Long unwinding"].includes(r.oi.label);
    tags.push(`<span class="tag ${good ? "g" : bad ? "r" : ""}">${esc(r.oi.label)}</span>`);
  }
  if (r.rvol >= 1.5) tags.push(`<span class="tag">Vol ${fmt(r.rvol, 1)}×</span>`);
  tags.push(mtfTags(r));
  const z = r.zone;
  const liq = r.stop_liquidity;
  return `<li class="row" data-sym="${esc(r.symbol)}">
    <span class="rk">${String(i + 1).padStart(2, "0")}</span>
    <div class="id"><span class="sym">${esc(r.symbol)}</span><span class="sec">${esc(r.sector)}</span></div>
    <div class="px">${fmt(r.ltp)}<span class="chg ${cls(r.change_pct)}">${pct(r.change_pct)}</span></div>
    <div class="meta"><div class="meter"><i style="width:${r.score}%"></i></div><span class="score">${r.score}</span>${tags.join("")}</div>
    <div class="levels">
      <span>R <b>${fmt(r.resistance)}</b></span>
      <span class="ob">OB <b>${z ? fmt(z.bottom) + "–" + fmt(z.top) : "—"}</b></span>
      <span>S <b>${fmt(r.support)}</b></span>
      <span>Liq <b>${fmt(liq)}</b></span>
    </div>
  </li>`;
}

$("#idx-strip").addEventListener("click", e => {
  const el = e.target.closest(".idx");
  if (el) location.hash = "chart/" + encodeURIComponent(el.dataset.sym);
});

function mtfTags(r) {
  if (!r.mtf) return "";
  const out = [];
  for (const [k, v] of Object.entries(r.mtf)) {
    if (!v || !v.align) continue;
    out.push(`<span class="tag ${v.align > 0 ? "g" : "r"}" title="${k} timeframe ${v.align > 0 ? "agrees" : "disagrees"}">${k} ${v.align > 0 ? "✓" : "✗"}</span>`);
  }
  if (r.htf_confluence && r.htf_confluence.length) out.push(`<span class="tag gold" title="The 5-min zone overlaps a higher-timeframe order block">HTF zone</span>`);
  return out.join("");
}

$("#board").addEventListener("click", e => {
  const row = e.target.closest(".row");
  if (row) location.hash = "chart/" + encodeURIComponent(row.dataset.sym);
});
$("#sector-filter").addEventListener("change", e => { state.sector = e.target.value; state.page = { bullish: 0, bearish: 0 }; loadBoard(); });
$("#board").addEventListener("click", e => {
  const b = e.target.closest(".pager button[data-dir]");
  if (!b || b.disabled) return;
  const side = b.closest(".pager").dataset.side;
  state.page[side] = Math.max(0, (state.page[side] || 0) + Number(b.dataset.dir));
  loadBoard().then(() => { const col = $(`.board .col[data-side="${side}"]`); if (col) col.scrollIntoView({ behavior: "smooth", block: "start" }); });
});
$("#side-toggle").addEventListener("click", e => {
  const b = e.target.closest("button"); if (!b) return;
  state.side = b.dataset.side;
  $$("#side-toggle button").forEach(x => x.classList.toggle("on", x === b));
  $$(".board .col").forEach(col => col.classList.toggle("show", col.dataset.side === state.side));
});

/* ================================================================ chart */
class ZonesPrimitive {
  constructor() { this.zones = []; this.chart = null; this.series = null; this.req = null; }
  attached({ chart, series, requestUpdate }) { this.chart = chart; this.series = series; this.req = requestUpdate; }
  detached() {}
  set(z) { this.zones = z; this.req && this.req(); }
  updateAllViews() {}
  paneViews() {
    const src = this;
    return [{
      zOrder: () => "bottom",
      renderer: () => ({
        draw(target) {
          target.useMediaCoordinateSpace(({ context: ctx, mediaSize }) => {
            if (!src.chart) return;
            const ts = src.chart.timeScale();
            for (const z of src.zones) {
              let x1 = ts.timeToCoordinate(z.from);
              if (x1 === null) x1 = 0;
              let x2 = z.to ? ts.timeToCoordinate(z.to) : null;
              if (x2 === null) x2 = mediaSize.width;
              const y1 = src.series.priceToCoordinate(z.top);
              const y2 = src.series.priceToCoordinate(z.bottom);
              if (y1 === null || y2 === null || x2 < 0) continue;
              const h = Math.max(2, y2 - y1);
              ctx.fillStyle = z.fill;
              ctx.fillRect(x1, y1, x2 - x1, h);
              ctx.strokeStyle = z.stroke; ctx.lineWidth = 1;
              ctx.setLineDash(z.dash ? [4, 3] : []);
              ctx.strokeRect(Math.round(x1) + .5, Math.round(y1) + .5, Math.round(x2 - x1), Math.round(h));
              ctx.setLineDash([]);
              if (z.label && x2 - x1 > 60) {
                ctx.font = "600 10.5px Segoe UI, system-ui, sans-serif";
                ctx.fillStyle = z.stroke;
                ctx.fillText(z.label, Math.max(x1, 0) + 5, y1 + (h > 14 ? 11 : -4));
              }
            }
          });
        },
      }),
    }];
  }
}

const chartUI = {};
function ensureChart() {
  if (chartUI.chart) return chartUI;
  const LC = window.LightweightCharts;
  const chart = LC.createChart($("#chart"), {
    autoSize: true,
    layout: { background: { type: "solid", color: "#10141b" }, textColor: "#8b94a5", fontFamily: "Segoe UI, system-ui, sans-serif", fontSize: 11 },
    grid: { vertLines: { color: "#161c25" }, horzLines: { color: "#161c25" } },
    rightPriceScale: { borderColor: "#1f2632", scaleMargins: { top: 0.08, bottom: 0.2 } },
    timeScale: { borderColor: "#1f2632", timeVisible: true, secondsVisible: false, rightOffset: 6 },
    crosshair: { mode: LC.CrosshairMode.Normal },
  });
  const candles = chart.addCandlestickSeries({
    upColor: "#1fbf75", downColor: "#f0525a", borderVisible: false, wickUpColor: "#1fbf75", wickDownColor: "#f0525a",
  });
  const volume = chart.addHistogramSeries({ priceFormat: { type: "volume" }, priceScaleId: "vol", lastValueVisible: false, priceLineVisible: false });
  chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.84, bottom: 0 } });
  const vwap = chart.addLineSeries({ color: "#5ea8ff", lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
  const zones = new ZonesPrimitive();
  candles.attachPrimitive(zones);
  Object.assign(chartUI, { chart, candles, volume, vwap, zones, lines: [], symShown: null });
  return chartUI;
}

async function loadChart() {
  await loadPicker();
  if (!state.sym) {
    const top = state.board && (state.board.bullish[0] || state.board.bearish[0]);
    if (!top) return;
    state.sym = top.symbol;
  }
  let d;
  try { d = await api(`/api/stock/${encodeURIComponent(state.sym)}?sessions=${state.sessions}&tf=${state.tf}`); }
  catch (e) { $("#c-sym").textContent = state.sym; $("#c-sub").textContent = e.message; return; }
  state.chartData = d;
  renderChart(d);
  renderZoneCard(d);
  const fresh = state.optSym !== d.symbol;
  if (fresh) state.expiry = "";
  state.optSym = d.symbol;
  if (fresh || !state.optAt || Date.now() - state.optAt > 20000) loadOptions(d);
  $$("#pick-list .pick").forEach(p => p.classList.toggle("on", p.dataset.sym === d.symbol));
}

function renderChart(d) {
  const ui = ensureChart();
  $("#c-sym").innerHTML = `${esc(d.symbol)} <span class="px">${fmt(d.ltp)}</span> <span class="px ${cls(d.change_pct)}" style="font-size:15px">${pct(d.change_pct)}</span>`;
  $("#c-sub").textContent = [d.name !== d.symbol ? d.name : null, d.sector, d.lot_size ? `Lot ${d.lot_size}` : null, ({ "5": "5-min", "15": "15-min", "60": "1-hour" }[d.tf] || "5-min") + " candles"].filter(Boolean).join(" · ");

  const bars = d.candles.map(c => ({ time: c.time + IST, open: c.open, high: c.high, low: c.low, close: c.close }));
  ui.candles.setData(bars);
  ui.volume.setData(d.candles.map(c => ({ time: c.time + IST, value: c.volume, color: c.close >= c.open ? "rgba(31,191,117,.35)" : "rgba(240,82,90,.35)" })));

  // VWAP for each session
  const vw = []; let day = null, pv = 0, vv = 0;
  for (const c of d.candles) {
    const dd = new Date((c.time + IST) * 1000).toISOString().slice(0, 10);
    if (dd !== day) { day = dd; pv = 0; vv = 0; }
    pv += (c.high + c.low + c.close) / 3 * c.volume; vv += c.volume;
    if (vv) vw.push({ time: c.time + IST, value: +(pv / vv).toFixed(2) });
  }
  ui.vwap.setData(vw);

  // order blocks
  const zones = d.order_blocks.map(o => {
    const isZone = d.zone && o.t === d.zone.t && o.kind === d.zone.kind;
    const bull = o.kind === "bull";
    const broken = o.status === "broken";
    const a = broken ? .06 : o.status === "tested" ? .12 : .18;
    return {
      from: o.t + IST, to: broken && o.broken_t ? o.broken_t + IST : null, top: o.top, bottom: o.bottom, dash: broken,
      fill: isZone ? `rgba(242,184,75,.22)` : bull ? `rgba(31,191,117,${a})` : `rgba(240,82,90,${a})`,
      stroke: isZone ? "#f2b84b" : bull ? (broken ? "rgba(31,191,117,.45)" : "#1fbf75") : (broken ? "rgba(240,82,90,.45)" : "#f0525a"),
      label: (isZone ? "TRADE ZONE · " : "") + (bull ? "Bull OB" : "Bear OB") + (o.status === "fresh" ? " · fresh" : o.status === "tested" ? ` · tested ${o.touches}×` : " · broken") + (o.fvg ? " · FVG" : ""),
    };
  });
  // trade zone last so it draws on top
  zones.sort((a, b) => (a.stroke === "#f2b84b") - (b.stroke === "#f2b84b"));
  ui.zones.set(zones);

  // price lines
  ui.lines.forEach(l => ui.candles.removePriceLine(l));
  ui.lines = [];
  const add = (price, color, title, style = 0, width = 1) => {
    if (price == null) return;
    ui.lines.push(ui.candles.createPriceLine({ price, color, title, lineStyle: style, lineWidth: width, axisLabelVisible: true }));
  };
  add(d.resistance, "#9aa3b2", "R", 0);
  add(d.support, "#9aa3b2", "S", 0);
  for (const lv of d.liquidity.slice(0, 6)) {
    const narrow = window.innerWidth < 760;
    add(lv.price, lv.side === "BSL" ? "rgba(94,168,255,.85)" : "rgba(196,140,255,.85)", narrow ? lv.side : `${lv.side} · ${lv.label}`, 2);
  }

  $("#legend").innerHTML =
    `<span><i style="background:rgba(31,191,117,.5)"></i>Bull OB</span><span><i style="background:rgba(240,82,90,.5)"></i>Bear OB</span>` +
    `<span><i style="background:#f2b84b"></i>Trade zone</span><span><i style="background:#5ea8ff"></i>VWAP · Buy-side liq.</span>` +
    `<span><i style="background:#c48cff"></i>Sell-side liq.</span>`;

  if (ui.symShown !== d.symbol + state.sessions + d.tf) {
    ui.chart.timeScale().fitContent();
    ui.symShown = d.symbol + state.sessions + d.tf;
  }
}

function renderZoneCard(d) {
  if (document.activeElement && document.activeElement.id === "pt-qty" && state.zoneSym === d.symbol) return;
  state.zoneSym = d.symbol;
  const biasCls = d.bias === "bullish" ? "up" : d.bias === "bearish" ? "down" : "";
  const z = d.zone;
  const lad = [];
  lad.push({ p: d.resistance, k: "Resistance" });
  if (d.target_liquidity != null) lad.push({ p: d.target_liquidity, k: "Target liquidity" });
  lad.push({ p: d.ltp, k: "Last price", c: "ltp" });
  if (z) lad.push({ p: (z.top + z.bottom) / 2, k: "Order block", c: "ob", v: `${fmt(z.bottom)}–${fmt(z.top)}` });
  lad.push({ p: d.support, k: "Support" });
  if (d.stop_liquidity != null) lad.push({ p: d.stop_liquidity, k: "Stop liquidity" });
  lad.sort((a, b) => b.p - a.p);

  const struct = d.structure > 0 ? "Broke up" : d.structure < 0 ? "Broke down" : "—";
  const zoneNote = !z ? (d.bias === "choppy" ? "Choppy: no clear direction, so no zone." : "No unbroken order block within a normal day's range. Wait for price to build one.")
    : d.in_zone ? "Price is inside the zone now." : `Zone edge is ${fmt(z.distance_pct)}% away${z.fvg ? ", and the move away from it left a fair-value gap" : ""}.`;

  $("#zone-card").innerHTML = `
    <div class="zc">
      <h3><span>Strength</span><span class="${biasCls}" style="text-transform:capitalize">${esc(d.bias)}</span></h3>
      <div class="big-score ${d.bias === "bearish" ? "bear" : "bull"}"><span class="n">${d.score}</span><div class="meter"><i style="width:${d.score}%"></i></div></div>
    </div>
    <div class="zc">
      <h3><span>Levels</span><span class="pill">${z ? esc(z.status) : "no zone"}</span></h3>
      <div class="ladder">${lad.map(x => `<div class="${x.c || ""}"><span>${x.k}</span><b>${x.v || fmt(x.p)}</b></div>`).join("")}</div>
      <p class="note">${zoneNote}</p>
    </div>
    <div class="zc">
      <h3><span>Reading</span></h3>
      <div class="kv">
        <span>Futures OI</span><b>${d.oi ? `${esc(d.oi.label)} (${pct(d.oi.oi_chg)})` : "—"}</b>
        <span>Structure</span><b>${struct}</b>
        <span>vs VWAP</span>${d.has_volume ? `<b class="${d.ltp > d.vwap ? "up" : "down"}">${d.ltp > d.vwap ? "Above" : "Below"} ${fmt(d.vwap)}</b>` : "<b>— (index)</b>"}
        ${Object.entries(d.mtf || {}).map(([k, v]) => `<span>${k} trend</span><b class="${v ? (v.align > 0 ? "up" : v.align < 0 ? "down" : "") : ""}">${!v ? "—" : (v.structure > 0 ? "Up" : v.structure < 0 ? "Down" : "Flat") + (v.in_bull_ob ? " · in bull OB" : v.in_bear_ob ? " · in bear OB" : "") + (v.align > 0 ? " ✓" : v.align < 0 ? " ✗" : "")}</b>`).join("")}
        <span>Move vs normal day</span><b>${fmt(d.norm_move, 2)}×</b>
        <span>Trend cleanliness</span><b>${Math.round(d.efficiency * 100)}%</b>
        <span>Relative volume</span><b>${fmt(d.rvol, 2)}×</b>
        <span>ATR (5-min)</span><b>${fmt(d.atr)}</b>
      </div>
      ${d.trap ? `<p class="note ${d.trap.dir > 0 ? "up" : "down"}">⚑ ${esc(d.trap.text)}</p>` : ""}
      ${d.htf_confluence && d.htf_confluence.length ? `<p class="note gold">◆ Zone overlaps a ${d.htf_confluence.join(" + ")} order block</p>` : ""}
    </div>
    ${d.plan ? `<div class="zc">
      <h3><span>Paper trade (${d.plan.direction === "bull" ? "long" : "short"} ${esc(d.symbol)})</span></h3>
      <div class="kv"><span>Entry</span><b>${fmt(d.plan.entry)}</b><span>Stop</span><b class="down">${fmt(d.plan.stop)}</b><span>Target</span><b class="up">${fmt(d.plan.target)}</b>
        <span>Qty</span><b><input class="qty" id="pt-qty" type="number" min="1" value="${(state.qty || {})[d.symbol] || d.lot_size || 1}"></b>
        <span>Reward : risk</span><b class="${d.plan.rr >= 1.5 ? "up" : "down"}">${fmt(d.plan.rr, 1)}</b></div>
      ${d.plan.rr < 1 ? `<p class="note">Reward is smaller than the risk from here. Wait for price to pull back and build a zone.</p>` : `
      <div class="f-actions" style="margin-top:10px">
        ${d.in_zone || !d.plan.from_zone ? `<button class="btn sm" id="pt-mkt">${d.plan.direction === "bull" ? "Buy" : "Sell"} now</button>` : ""}
        ${d.plan.from_zone ? `<button class="btn sm" id="pt-lmt">Limit at zone ${fmt(d.plan.entry)}</button>` : ""}
      </div>`}
    </div>` : ""}
    <button class="btn primary" id="log-zone">${z ? "Log a trade from this zone" : "Log a trade"}</button>`;
  const ptOpen = async (orderType) => {
    try {
      await api("/api/paper", { method: "POST", body: JSON.stringify({
        symbol: d.symbol, direction: d.plan.direction, u_entry: d.plan.entry, u_stop: d.plan.stop, u_target: d.plan.target,
        qty: +$("#pt-qty").value || 1, order_type: orderType }) });
      toast(orderType === "MARKET" ? "Paper trade opened" : "Limit order placed at the zone");
    } catch (e) { toast(e.message); }
  };
  if ($("#pt-qty")) $("#pt-qty").oninput = e => { state.qty = state.qty || {}; state.qty[d.symbol] = e.target.value; };
  if ($("#pt-mkt")) $("#pt-mkt").onclick = () => ptOpen("MARKET");
  if ($("#pt-lmt")) $("#pt-lmt").onclick = () => ptOpen("LIMIT");
  $("#log-zone").onclick = () => {
    const long = d.bias !== "bearish";
    const entry = z ? (long ? z.top : z.bottom) : d.ltp;
    const stop = z ? (long ? z.bottom - d.atr * 0.25 : z.top + d.atr * 0.25) : (long ? d.support : d.resistance);
    const target = (long ? d.target_liquidity ?? d.resistance : d.target_liquidity ?? d.support);
    state.prefill = { symbol: d.symbol, side: long ? "LONG" : "SHORT", entry: +entry.toFixed(2), stop: +stop.toFixed(2),
      target: target != null ? +(+target).toFixed(2) : "", setup: d.trap ? "Liquidity sweep" : "OB retest", qty: d.lot_size || 1 };
    location.hash = "journal";
  };
}

async function loadPicker() {
  if (!state.board) state.board = await api("/api/board?limit=250");
  if (!state.symbols.length) state.symbols = await api("/api/symbols");
  const q = $("#sym-search").value.trim().toUpperCase();
  const item = (s, chg) => `<div class="pick" data-sym="${esc(s)}"><span>${esc(s)}</span><span class="${cls(chg)}">${chg == null ? "" : pct(chg)}</span></div>`;
  let html;
  if (q) {
    html = state.symbols.filter(s => s.includes(q)).slice(0, 60).map(s => item(s)).join("") || `<div class="hint" style="padding:8px">No match</div>`;
  } else {
    html = `<h3>Indices</h3>` + (state.board.indices || []).map(r => item(r.symbol, r.change_pct)).join("") +
           `<h3>Top bullish</h3>` + state.board.bullish.slice(0, 10).map(r => item(r.symbol, r.change_pct)).join("") +
           `<h3>Top bearish</h3>` + state.board.bearish.slice(0, 10).map(r => item(r.symbol, r.change_pct)).join("");
  }
  $("#pick-list").innerHTML = html;
  $$("#pick-list .pick").forEach(p => p.classList.toggle("on", p.dataset.sym === state.sym));
}
$("#pick-list").addEventListener("click", e => {
  const p = e.target.closest(".pick");
  if (p) location.hash = "chart/" + encodeURIComponent(p.dataset.sym);
});
$("#sym-search").addEventListener("input", loadPicker);
$("#sym-search").addEventListener("keydown", e => {
  if (e.key === "Enter") { const first = $("#pick-list .pick"); if (first) location.hash = "chart/" + first.dataset.sym; }
});
$("#tf-toggle").addEventListener("click", e => {
  const b = e.target.closest("button"); if (!b) return;
  state.tf = b.dataset.tf;
  $$("#tf-toggle button").forEach(x => x.classList.toggle("on", x === b));
  loadChart();
});
$("#sess-toggle").addEventListener("click", e => {
  const b = e.target.closest("button"); if (!b) return;
  state.sessions = +b.dataset.s;
  $$("#sess-toggle button").forEach(x => x.classList.toggle("on", x === b));
  loadChart();
});

/* ================================================================ x-ray */
async function loadXray() {
  const d = await api("/api/xray");
  const b = d.breadth, tot = b.bullish + b.bearish + b.choppy || 1;
  const lead = d.sectors[0], lag = d.sectors[d.sectors.length - 1];
  $("#xr-top").innerHTML = `
    <div class="tile"><div class="k">Advancing / declining</div><div class="v"><span class="up">${b.advancing}</span> <small>/</small> <span class="down">${b.declining}</span></div></div>
    <div class="tile"><div class="k">Setups: bullish · choppy · bearish</div><div class="v">${b.bullish} <small>·</small> ${b.choppy} <small>·</small> ${b.bearish}</div>
      <div class="breadth"><i style="width:${b.bullish / tot * 100}%"></i><i style="width:${b.choppy / tot * 100}%"></i><i style="width:${b.bearish / tot * 100}%"></i></div></div>
    <div class="tile"><div class="k">Leading sector</div><div class="v up">${esc(lead ? lead.sector : "—")} <small>${lead ? pct(lead.avg_change) : ""}</small></div></div>
    <div class="tile"><div class="k">Lagging sector</div><div class="v down">${esc(lag ? lag.sector : "—")} <small>${lag ? pct(lag.avg_change) : ""}</small></div></div>`;
  drawScatter(d.points);
  const mx = Math.max(0.5, ...d.sectors.map(s => Math.abs(s.avg_change)));
  $("#sectors").innerHTML = d.sectors.map(s => {
    const w = Math.abs(s.avg_change) / mx * 50;
    const pos = s.avg_change >= 0;
    return `<div class="sec-row"><span>${esc(s.sector)}</span>
      <div class="bar"><i style="${pos ? `left:50%` : `right:50%`};width:${w}%;background:${pos ? "var(--bull)" : "var(--bear)"}"></i></div>
      <span class="v ${cls(s.avg_change)}">${pct(s.avg_change)}</span>
      <span class="cnt">${s.n} stocks · ${s.bullish} bull · ${s.bearish} bear · ${s.choppy} choppy</span></div>`;
  }).join("");
}

function drawScatter(points) {
  const box = $("#scatter");
  const W = box.clientWidth || 800, H = box.clientHeight || 500;
  const pad = { l: 44, r: 20, t: 14, b: 30 };
  const mxX = Math.max(1, ...points.map(p => Math.abs(p.change_pct))) * 1.08;
  const x = v => pad.l + (v + mxX) / (2 * mxX) * (W - pad.l - pad.r);
  const y = v => pad.t + (100 - v) / 200 * (H - pad.t - pad.b);
  const signed = p => p.bias === "bullish" ? p.score : p.bias === "bearish" ? -p.score : 0;
  const color = p => p.bias === "bullish" ? "#1fbf75" : p.bias === "bearish" ? "#f0525a" : "#5d6677";
  const ticksX = []; const step = mxX > 6 ? 2 : mxX > 3 ? 1 : 0.5;
  for (let v = -Math.floor(mxX / step) * step; v <= mxX; v += step) ticksX.push(+v.toFixed(2));
  const labelled = [...points].sort((a, b) => Math.abs(signed(b)) - Math.abs(signed(a))).slice(0, 10).map(p => p.symbol);
  let svg = `<svg viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg">`;
  for (const v of [-100, -50, 0, 50, 100]) svg += `<line x1="${pad.l}" x2="${W - pad.r}" y1="${y(v)}" y2="${y(v)}" stroke="${v ? "#161c25" : "#2a3342"}"/>` +
    `<text x="${pad.l - 8}" y="${y(v) + 4}" fill="#5d6677" font-size="10.5" text-anchor="end">${v > 0 ? "+" : ""}${v}</text>`;
  for (const v of ticksX) svg += `<line y1="${pad.t}" y2="${H - pad.b}" x1="${x(v)}" x2="${x(v)}" stroke="${v ? "#161c25" : "#2a3342"}"/>` +
    `<text x="${x(v)}" y="${H - pad.b + 16}" fill="#5d6677" font-size="10.5" text-anchor="middle">${v > 0 ? "+" : ""}${v}%</text>`;
  for (const p of points) {
    svg += `<circle class="dot" data-sym="${esc(p.symbol)}" cx="${x(p.change_pct).toFixed(1)}" cy="${y(signed(p)).toFixed(1)}" r="${p.bias === "choppy" ? 3 : 4.5}" fill="${color(p)}" fill-opacity="${p.bias === "choppy" ? .5 : .85}"/>`;
    if (labelled.includes(p.symbol)) {
      const right = x(p.change_pct) > W - 90;
      svg += `<text x="${x(p.change_pct) + (right ? -7 : 7)}" y="${y(signed(p)) + 3.5}" text-anchor="${right ? "end" : "start"}" fill="#c9cfda" font-size="10.5" pointer-events="none">${esc(p.symbol)}</text>`;
    }
  }
  svg += `</svg><div class="tip" hidden></div>`;
  box.innerHTML = svg;
  const tip = $(".tip", box);
  const byS = Object.fromEntries(points.map(p => [p.symbol, p]));
  box.onmousemove = e => {
    const c = e.target.closest(".dot");
    if (!c) return (tip.hidden = true);
    const p = byS[c.dataset.sym];
    tip.hidden = false;
    tip.innerHTML = `<b>${esc(p.symbol)}</b> · ${esc(p.sector)}<br>${pct(p.change_pct)} · ${p.bias} ${p.score || ""}`;
    const r = box.getBoundingClientRect();
    tip.style.left = Math.min(e.clientX - r.left + 12, r.width - 170) + "px";
    tip.style.top = (e.clientY - r.top + 12) + "px";
  };
  box.onmouseleave = () => (tip.hidden = true);
  box.onclick = e => { const c = e.target.closest(".dot"); if (c) location.hash = "chart/" + c.dataset.sym; };
}
window.addEventListener("resize", () => { if (state.view === "xray" && state.status && state.status.ready) loadXray(); });

/* ================================================================ journal */
const jForm = $("#j-form");
async function loadJournal() {
  const { trades, stats } = await api("/api/journal");
  const tile = (k, v, c = "") => `<div class="tile"><div class="k">${k}</div><div class="v ${c}">${v}</div></div>`;
  $("#j-stats").innerHTML =
    tile("Closed trades", stats.closed) +
    tile("Win rate", stats.win_rate == null ? "—" : stats.win_rate + "%") +
    tile("Net P&amp;L", "₹" + fmt(stats.net_pnl), cls(stats.net_pnl)) +
    tile("Average R", stats.avg_r == null ? "—" : fmt(stats.avg_r) + "R", cls(stats.avg_r)) +
    tile("Profit factor", stats.profit_factor == null ? "—" : fmt(stats.profit_factor));
  $("#j-count").textContent = `${trades.length} logged · ${trades.filter(t => t.status === "OPEN").length} open`;
  $("#j-table").innerHTML = trades.length ? `<thead><tr><th>Date</th><th>Symbol</th><th>Side</th><th class="n">Qty</th><th class="n">Entry</th><th class="n">Stop</th><th class="n">Exit</th><th class="n">P&amp;L</th><th class="n">R</th><th>Setup</th><th></th></tr></thead><tbody>` +
    trades.map(t => `<tr data-id="${t.id}">
      <td>${esc(t.trade_date)}</td><td><a href="#chart/${esc(t.symbol)}"><b>${esc(t.symbol)}</b></a></td>
      <td class="${t.side === "LONG" ? "up" : "down"}">${t.side}</td><td class="n">${fmt(t.qty, 0)}</td>
      <td class="n">${fmt(t.entry)}</td><td class="n">${fmt(t.stop)}</td><td class="n">${t.exit == null ? `<span class="pill">open</span>` : fmt(t.exit)}</td>
      <td class="n ${cls(t.pnl)}">${t.pnl == null ? "—" : fmt(t.pnl)}</td><td class="n ${cls(t.r_multiple)}">${t.r_multiple == null ? "—" : fmt(t.r_multiple) + "R"}</td>
      <td>${esc(t.setup)}</td>
      <td><div class="acts"><button class="btn sm" data-act="edit">Edit</button><button class="btn sm ghost" data-act="del">Delete</button></div></td></tr>`).join("") + "</tbody>"
    : `<tbody><tr><td class="empty">No trades yet. Log one with the form, or from a chart's zone card.</td></tr></tbody>`;
  $("#j-table").dataset.rows = JSON.stringify(trades);
  const setups = Object.entries(stats.by_setup);
  $("#j-setups").innerHTML = setups.length ? setups.map(([k, s]) =>
    `<div class="setup-row"><span>${esc(k)}</span><span>${s.trades} trades · ${Math.round(s.wins / s.trades * 100)}% wins · <b class="${cls(s.pnl)}">₹${fmt(s.pnl)}</b></span></div>`).join("")
    : `<p class="hint">Closed trades are grouped by setup here, so you can see which setup actually makes money.</p>`;

  if (!jForm.trade_date.value) jForm.trade_date.value = new Date(Date.now() + IST * 1000).toISOString().slice(0, 10);
  if (state.prefill) {
    resetForm();
    for (const [k, v] of Object.entries(state.prefill)) if (jForm[k]) jForm[k].value = v;
    state.prefill = null;
    jForm.exit.focus();
    toast("Trade pre-filled from the chart zone. Check it, then save.");
  }
  $("#sym-options").innerHTML = state.symbols.map(s => `<option value="${esc(s)}">`).join("");
}

function resetForm() {
  jForm.reset();
  jForm.id.value = "";
  jForm.trade_date.value = new Date(Date.now() + IST * 1000).toISOString().slice(0, 10);
  $("#j-form-title").textContent = "Log a trade";
  $("#j-submit").textContent = "Save trade";
  $("#j-cancel").hidden = true;
}

jForm.addEventListener("submit", async e => {
  e.preventDefault();
  const body = Object.fromEntries(new FormData(jForm));
  const id = body.id; delete body.id;
  try {
    if (id) await api(`/api/journal/${id}`, { method: "PUT", body: JSON.stringify(body) });
    else await api("/api/journal", { method: "POST", body: JSON.stringify(body) });
    toast(id ? "Trade updated" : "Trade saved");
    resetForm();
    loadJournal();
  } catch (err) { toast(err.message); }
});
$("#j-cancel").addEventListener("click", resetForm);
$("#j-table").addEventListener("click", async e => {
  const btn = e.target.closest("button[data-act]"); if (!btn) return;
  const id = btn.closest("tr").dataset.id;
  if (btn.dataset.act === "del") {
    if (btn.dataset.armed) { await api(`/api/journal/${id}`, { method: "DELETE" }); toast("Trade deleted"); loadJournal(); }
    else { btn.dataset.armed = 1; btn.textContent = "Sure?"; setTimeout(() => { btn.textContent = "Delete"; delete btn.dataset.armed; }, 3000); }
    return;
  }
  const t = JSON.parse($("#j-table").dataset.rows).find(r => String(r.id) === id);
  for (const k of ["trade_date", "symbol", "side", "qty", "entry", "stop", "target", "exit", "setup", "notes"]) jForm[k].value = t[k] ?? "";
  jForm.id.value = id;
  $("#j-form-title").textContent = `Edit trade #${id}`;
  $("#j-submit").textContent = "Update trade";
  $("#j-cancel").hidden = false;
  jForm.scrollIntoView({ behavior: "smooth", block: "start" });
});


/* ================================================================ options helper */
async function loadOptions(d) {
  state.optAt = Date.now();
  const box = $("#opt-panel");
  let res;
  try { res = await api(`/api/options/${encodeURIComponent(d.symbol)}?expiry=${encodeURIComponent(state.expiry || "")}`); }
  catch (e) { box.innerHTML = `<div class="panel-h"><h2>Options</h2></div><p class="hint">${esc(e.message)}</p>`; return; }
  if (state.chartData && state.chartData.symbol !== d.symbol) return;     // user moved on
  const h = res.helper;
  const expSel = res.expiries.length ? `<select id="opt-exp">${res.expiries.map(x => `<option ${h && x === h.expiry ? "selected" : ""}>${x}</option>`).join("")}</select>` : "";
  if (!h) { box.innerHTML = `<div class="panel-h"><h2>Options</h2>${expSel}</div><p class="hint">${esc(res.error || "No options data.")}</p>`; bindExp(d); return; }
  state.expiry = h.expiry;
  const oiLine = `<div class="opt-oi">
      <span>PCR <b class="mono">${h.pcr ?? "—"}</b></span>
      <span>OI support <b class="mono up">${fmt(h.oi_support, h.oi_support % 1 ? 1 : 0)}</b></span>
      <span>OI resistance <b class="mono down">${fmt(h.oi_resistance, h.oi_resistance % 1 ? 1 : 0)}</b></span>
      <span>Lot <b class="mono">${h.lot_size}</b></span>
      ${h.days_left != null ? `<span>${h.days_left} day${h.days_left === 1 ? "" : "s"} to expiry</span>` : ""}</div>`;
  const warn = (h.warnings || []).map(w => `<p class="opt-warn">${esc(w)}</p>`).join("");
  let table = "";
  if (h.candidates && h.candidates.length) {
    const p = h.plan;
    table = `<p class="hint" style="margin:6px 0 8px">Plan on ${esc(d.symbol)}: entry <b class="mono">${fmt(p.entry)}</b> · stop <b class="mono down">${fmt(p.stop)}</b> · target <b class="mono up">${fmt(p.target)}</b>. Option prices below are estimated for those levels.</p>
    <div class="table-wrap"><table class="trades opt-table"><thead><tr>
      <th></th><th>Strike</th><th class="n">Premium</th><th class="n">Δ</th><th class="n">IV</th><th class="n">OI chg</th>
      <th class="n">Entry · Stop · Target</th><th class="n">R:R</th><th class="n">Cost/lot</th><th class="n">Risk/lot</th><th class="n" title="Premium lost per lot per day from time decay">Decay/day</th><th></th></tr></thead><tbody>
      ${h.candidates.map((c, i) => `<tr class="${c.recommended ? "rec" : ""}">
        <td><span class="tag ${c.recommended ? "gold" : ""}">${c.tag}${c.recommended ? " ★" : ""}</span></td>
        <td><b>${fmt(c.strike, c.strike % 1 ? 1 : 0)} ${h.side}</b></td>
        <td class="n">${fmt(c.ltp)}</td><td class="n">${fmt(c.delta)}</td><td class="n">${fmt(c.iv, 1)}%</td>
        <td class="n ${cls(c.oi_chg_pct)}">${c.oi_chg_pct == null ? "—" : (c.oi_chg_pct > 0 ? "+" : "") + c.oi_chg_pct + "%"}</td>
        <td class="n">${fmt(c.entry)} · <span class="down">${fmt(c.stop)}</span> · <span class="up">${fmt(c.target)}</span></td>
        <td class="n ${c.rr >= 1.5 ? "up" : "down"}">${fmt(c.rr, 1)}</td>
        <td class="n">₹${fmt(c.cost_per_lot, 0)}</td><td class="n down">₹${fmt(c.risk_per_lot, 0)}</td><td class="n down">₹${fmt(Math.abs(c.theta_per_lot), 0)}</td>
        <td><button class="btn sm" data-opt="${i}">Paper buy</button></td></tr>`).join("")}
    </tbody></table></div>`;
  }
  box.innerHTML = `<div class="panel-h"><h2>Options helper ${h.side ? `<span class="tag ${h.side === "CE" ? "g" : "r"}">${h.side === "CE" ? "Calls (bullish)" : "Puts (bearish)"}</span>` : ""}</h2>${expSel}</div>${oiLine}${warn}${table}
    <p class="muted small">★ = delta closest to 0.55, a common balance between cost and responsiveness. Estimates assume the move happens today; time decay and IV changes will shift real prices.</p>`;
  bindExp(d);
  $$("button[data-opt]", box).forEach(b => b.onclick = async () => {
    const c = h.candidates[+b.dataset.opt];
    try {
      await api("/api/paper", { method: "POST", body: JSON.stringify({
        symbol: d.symbol, instrument: "OPTION", price_key: c.key, label: c.label, premium: c.ltp,
        direction: h.plan.direction, u_entry: h.plan.entry, u_stop: h.plan.stop, u_target: h.plan.target,
        qty: h.lot_size, order_type: "MARKET" }) });
      toast(`Paper bought 1 lot ${c.label}`);
    } catch (e) { toast(e.message); }
  });
}
function bindExp(d) {
  const sel = $("#opt-exp");
  if (sel) sel.onchange = () => { state.expiry = sel.value; loadOptions(d); };
}

/* ================================================================ paper trading */
async function loadPaper() {
  const d = await api("/api/paper");
  const st = d.stats;
  const tile = (k, v, c = "") => `<div class="tile"><div class="k">${k}</div><div class="v ${c}">${v}</div></div>`;
  $("#p-stats").innerHTML =
    tile("Open P&amp;L", "₹" + fmt(st.unrealised), cls(st.unrealised)) +
    tile("Realised P&amp;L", "₹" + fmt(st.realised), cls(st.realised)) +
    tile("Closed trades", st.closed) +
    tile("Win rate", st.win_rate == null ? "—" : st.win_rate + "%") +
    tile("Profit factor", st.profit_factor == null ? "—" : fmt(st.profit_factor));
  $("#p-eod").textContent = d.eod ? "Auto square-off at 15:20" : "No auto square-off";
  const active = d.rows.filter(r => r.status === "OPEN" || r.status === "PENDING");
  const done = d.rows.filter(r => r.status === "CLOSED" || r.status === "CANCELLED");
  const sideTag = r => r.instrument === "OPTION" ? `<span class="tag">option</span>` : `<span class="${r.side === "LONG" ? "up" : "down"}">${r.side}</span>`;
  $("#p-open").innerHTML = active.length ? `<thead><tr><th>Opened</th><th>Instrument</th><th>Side</th><th class="n">Qty</th><th class="n">Fill</th><th class="n">Now</th><th class="n">P&amp;L</th><th class="n">Underlying</th><th class="n">Stop / target</th><th>Status</th><th></th></tr></thead><tbody>` +
    active.map(r => `<tr><td>${esc(r.created.slice(5))}</td><td><a href="#chart/${esc(r.symbol)}"><b>${esc(r.label)}</b></a></td><td>${sideTag(r)}</td>
      <td class="n">${fmt(r.qty, 0)}</td><td class="n">${r.fill_price == null ? "—" : fmt(r.fill_price)}</td><td class="n">${fmt(r.last_price)}</td>
      <td class="n ${cls(r.pnl)}">${r.pnl == null ? "—" : "₹" + fmt(r.pnl)}</td><td class="n">${fmt(r.u_ltp)}</td>
      <td class="n"><span class="down">${fmt(r.u_stop)}</span> / <span class="up">${fmt(r.u_target)}</span></td>
      <td>${r.status === "PENDING" ? `<span class="pill">waiting for ${fmt(r.u_entry)}</span>` : `<span class="pill ok">open</span>`}</td>
      <td><button class="btn sm" data-close="${r.id}">${r.status === "PENDING" ? "Cancel" : "Close now"}</button></td></tr>`).join("") + "</tbody>"
    : `<tbody><tr><td class="empty">No open paper trades. Open one from a chart's zone card or the options helper.</td></tr></tbody>`;
  $("#p-count").textContent = `${done.length} trades`;
  $("#p-closed").innerHTML = done.length ? `<thead><tr><th>Closed</th><th>Instrument</th><th>Side</th><th class="n">Fill</th><th class="n">Exit</th><th class="n">P&amp;L</th><th>Reason</th><th></th></tr></thead><tbody>` +
    done.map(r => `<tr><td>${esc((r.exit_time || "").slice(5))}</td><td><b>${esc(r.label)}</b></td><td>${sideTag(r)}</td>
      <td class="n">${r.fill_price == null ? "—" : fmt(r.fill_price)}</td><td class="n">${r.exit_price == null ? "—" : fmt(r.exit_price)}</td>
      <td class="n ${cls(r.pnl)}">${r.pnl == null ? "—" : "₹" + fmt(r.pnl)}</td>
      <td><span class="tag ${r.exit_reason === "TARGET" ? "g" : r.exit_reason === "STOP" ? "r" : ""}">${esc(r.exit_reason || r.status)}</span></td>
      <td><button class="btn sm ghost" data-del="${r.id}">Remove</button></td></tr>`).join("") + "</tbody>"
    : `<tbody><tr><td class="empty">Nothing closed yet.</td></tr></tbody>`;
  drawCurve(st.curve);
  $("#p-exits").innerHTML = Object.keys(st.exits).length ? `<div class="setup-row"><span>Exits</span><span>${Object.entries(st.exits).map(([k, v]) => `${esc(k)} <b>${v}</b>`).join(" · ")}</span></div>` : "";
}
function drawCurve(curve) {
  const box = $("#p-curve");
  if (!curve || curve.length < 2) { box.innerHTML = `<p class="hint">The curve appears after two closed trades.</p>`; return; }
  const W = box.clientWidth || 400, H = 160, pad = 8;
  const pts = [0, ...curve];
  const mn = Math.min(...pts), mx = Math.max(...pts), span = mx - mn || 1;
  const x = i => pad + i / (pts.length - 1) * (W - 2 * pad);
  const y = v => pad + (mx - v) / span * (H - 2 * pad);
  const path = pts.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("");
  const last = pts[pts.length - 1];
  box.innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}"><line x1="${pad}" x2="${W - pad}" y1="${y(0)}" y2="${y(0)}" stroke="#2a3342" stroke-dasharray="3 3"/>
    <path d="${path}" fill="none" stroke="${last >= 0 ? "#1fbf75" : "#f0525a"}" stroke-width="2"/></svg>`;
}
$("#view-paper").addEventListener("click", async e => {
  const c = e.target.closest("[data-close]"), d = e.target.closest("[data-del]");
  try {
    if (c) { await api(`/api/paper/${c.dataset.close}/close`, { method: "POST" }); toast("Paper trade closed"); loadPaper(); }
    if (d) { await api(`/api/paper/${d.dataset.del}`, { method: "DELETE" }); loadPaper(); }
  } catch (err) { toast(err.message); }
});

/* ================================================================ alerts */
let audioCtx = null;
function beep(dir) {
  try {
    audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
    const notes = dir > 0 ? [660, 880] : dir < 0 ? [660, 440] : [740, 740];
    notes.forEach((f, i) => {
      const o = audioCtx.createOscillator(), g = audioCtx.createGain();
      o.frequency.value = f; o.type = "sine";
      g.gain.setValueAtTime(0.0001, audioCtx.currentTime + i * 0.18);
      g.gain.exponentialRampToValueAtTime(0.25, audioCtx.currentTime + i * 0.18 + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, audioCtx.currentTime + i * 0.18 + 0.16);
      o.connect(g); g.connect(audioCtx.destination);
      o.start(audioCtx.currentTime + i * 0.18); o.stop(audioCtx.currentTime + i * 0.18 + 0.17);
    });
  } catch (_) {}
}
function renderAlerts() {
  const b = $("#bell-badge");
  b.hidden = !state.unread; b.textContent = state.unread > 99 ? "99+" : state.unread;
  $("#alert-list").innerHTML = state.alerts.length ? [...state.alerts].reverse().slice(0, 60).map(a => `
    <div class="ap-item ${a.dir > 0 ? "bull" : a.dir < 0 ? "bear" : ""}" data-sym="${esc(a.symbol)}">
      <div><b>${esc(a.title)}</b><span class="muted small">${esc(a.time)}</span></div>
      <div class="muted small">${esc(a.text)}</div></div>`).join("") : `<p class="hint">No alerts yet.</p>`;
}
async function fetchAlerts(silent = false) {
  try {
    const d = await api(`/api/alerts?since=${silent ? 0 : state.lastAlert || 0}`);
    if (!d.items.length) { state.lastAlert = d.last_id; return; }
    if (silent) state.alerts = d.items;
    else {
      state.alerts.push(...d.items);
      state.unread += d.items.length;
      const last = d.items[d.items.length - 1];
      toast(`🔔 ${last.title}`);
      if (state.sound) beep(last.dir);
      if (state.desktop && "Notification" in window && Notification.permission === "granted") {
        for (const a of d.items.slice(-3)) {
          try { const n = new Notification(a.title, { body: a.text, tag: "br" + a.id }); n.onclick = () => { window.focus(); location.hash = "chart/" + a.symbol; }; } catch (_) {}
        }
      }
    }
    state.alerts = state.alerts.slice(-200);
    state.lastAlert = d.last_id;
    renderAlerts();
  } catch (_) {}
}
$("#bell").addEventListener("click", e => {
  e.stopPropagation();
  const p = $("#alert-panel");
  p.hidden = !p.hidden;
  if (!p.hidden) { state.unread = 0; renderAlerts(); }
});
$("#alert-clear").addEventListener("click", () => { state.unread = 0; renderAlerts(); $("#alert-panel").hidden = true; });
$("#alert-list").addEventListener("click", e => {
  const it = e.target.closest(".ap-item");
  if (it && it.dataset.sym !== "TEST") { $("#alert-panel").hidden = true; location.hash = "chart/" + it.dataset.sym; }
});
document.addEventListener("click", e => { if (!e.target.closest(".bell-wrap")) $("#alert-panel").hidden = true; });

/* ================================================================ settings / brokers */
const BADGE = { ready: ["Ready", "ok"], beta: ["Beta", "gold"], unsupported: ["Not supported", "bad"] };

function brokerCards(s, selected, forPicker) {
  const demo = { key: "demo", name: "Demo", status: "ready", cost: "Simulated prices, no account needed", connected: true };
  return [...s.brokers, demo].map(b => {
    const live = s.mode === b.key;
    const [label, cls] = live ? ["In use", "ok"] : b.key === "demo" ? ["Try it", ""] : (b.connected ? ["Connected", "ok"] : BADGE[b.status]);
    return `<button class="bcard ${b.status} ${selected === b.key ? "on" : ""}" data-broker="${b.key}" ${b.status === "unsupported" && forPicker ? "" : ""}>
      <span class="bname">${esc(b.name)}</span>
      <span class="pill ${cls}">${label}</span>
      <span class="bcost">${esc(b.status === "unsupported" ? "Its API can't support BlockRadar yet" : b.cost)}</span>
    </button>`;
  }).join("");
}

async function loadSettings() {
  const s = await api("/api/settings");
  state.settings = s;
  const sel = state.selBroker || s.mode;
  $("#broker-grid").innerHTML = brokerCards(s, sel, false);
  const cur = s.brokers.find(b => b.key === s.mode);
  const pill = $("#src-pill");
  pill.textContent = s.mode === "demo" ? "Demo data" : `${cur ? cur.name : s.mode}: ${cur && cur.connected ? "connected" : "login needed"}`;
  pill.className = "pill " + (s.mode === "demo" ? "" : cur && cur.connected ? "ok" : "bad");
  renderBrokerForm(s, sel);

  const af = $("#al-form"), al = s.alerts || {};
  for (const k of ["zone", "trap", "top", "paper"]) af[k].checked = !!al[k];
  af.min_score.value = al.min_score ?? 60;
  af.cooldown_min.value = al.cooldown_min ?? 20;
  af.telegram_token.value = al.telegram_token ? "saved" : "";
  af.telegram_chat.value = al.telegram_chat || "";
  af.paper_eod.checked = !!s.paper_eod;
  $("#al-sound").checked = state.sound;
  $("#al-desktop").checked = state.desktop && "Notification" in window && Notification.permission === "granted";
}

function renderBrokerForm(s, key) {
  const box = $("#broker-form");
  if (key === "demo") {
    box.innerHTML = `<div class="bform"><h3>Demo mode</h3><p class="muted">A simulated market that moves in fast-forward, so you can try every screen without a broker.</p>
      <div class="f-actions"><button class="btn primary" id="use-demo" ${s.mode === "demo" ? "disabled" : ""}>${s.mode === "demo" ? "In use" : "Use demo data"}</button></div></div>`;
    $("#use-demo").onclick = () => switchMode("demo");
    return;
  }
  const b = s.brokers.find(x => x.key === key);
  if (!b) { box.innerHTML = ""; return; }
  if (b.status === "unsupported") {
    box.innerHTML = `<div class="bform"><h3>${esc(b.name)}</h3><p class="opt-warn">${esc(b.reason)}</p></div>`;
    return;
  }
  const inUse = s.mode === b.key;
  const fields = b.fields.map(f => `<label>${esc(f.label)}${f.secret && f.saved ? ` <small>(saved; leave blank to keep)</small>` : ""}
      <input name="${f.key}" ${f.secret ? 'type="password" placeholder="••••••••"' : ""} value="${esc(f.value)}" spellcheck="false" autocomplete="off"></label>`).join("");
  const connectBtn = b.auth === "oauth"
    ? `<button type="button" class="btn primary" id="b-connect">${b.connected ? "Reconnect" : "Connect"} ${esc(b.name)}</button>`
    : `<button type="submit" class="btn primary" data-connect="1">Save &amp; connect</button>`;
  box.innerHTML = `<form class="bform" id="b-form" autocomplete="off">
      <h3>${esc(b.name)} ${b.status === "beta" ? `<span class="pill gold" title="Built from the official API docs and tested against a simulated API; not yet confirmed with a real account">Beta</span>` : ""}
        <span class="pill ${b.connected ? "ok" : ""}">${b.connected ? "Logged in today" : "Not logged in"}</span></h3>
      <p class="muted small">${esc(b.cost)}</p>
      ${fields}
      <div class="f-actions">
        ${b.auth === "oauth" ? `<button type="submit" class="btn">Save keys</button>` : ""}
        ${connectBtn}
        ${!inUse && b.connected ? `<button type="button" class="btn" id="b-use">Use ${esc(b.name)}</button>` : ""}
      </div>
      <ol class="steps">${b.steps.map(x => `<li>${esc(x)}</li>`).join("")}</ol>
    </form>`;
  const collect = () => Object.fromEntries(new FormData($("#b-form")));
  $("#b-form").onsubmit = async e => {
    e.preventDefault();
    const connect = e.submitter && e.submitter.dataset.connect;
    await api("/api/settings", { method: "POST", body: JSON.stringify({ broker: b.key, creds: collect(), ...(connect ? { mode: b.key } : {}) }) });
    toast(connect ? `Connecting to ${b.name}…` : "Keys saved");
    if (connect) resetData();
    loadSettings();
  };
  if ($("#b-connect")) $("#b-connect").onclick = async () => {
    await api("/api/settings", { method: "POST", body: JSON.stringify({ broker: b.key, creds: collect() }) });
    const fresh = (await api("/api/settings")).brokers.find(x => x.key === b.key);
    if (!fresh.configured) { toast("Fill in the keys first"); loadSettings(); return; }
    location.href = `/auth/${b.key}/login`;
  };
  if ($("#b-use")) $("#b-use").onclick = () => switchMode(b.key);
}

function resetData() { state.board = null; state.symbols = []; state.lastUpdated = 0; state.optSym = null; }

async function switchMode(mode) {
  await api("/api/settings", { method: "POST", body: JSON.stringify({ mode }) });
  resetData();
  toast(mode === "demo" ? "Switched to demo data" : "Switched broker. Loading data…");
  loadSettings();
}

$("#broker-grid").addEventListener("click", e => {
  const c = e.target.closest(".bcard"); if (!c) return;
  state.selBroker = c.dataset.broker;
  $$("#broker-grid .bcard").forEach(x => x.classList.toggle("on", x === c));
  renderBrokerForm(state.settings, state.selBroker);
});

async function maybeShowPicker(status) {
  if (status.picked || maybeShowPicker.done) return;
  maybeShowPicker.done = true;
  const s = await api("/api/settings");
  $("#picker-grid").innerHTML = brokerCards(s, null, true);
  $("#picker").hidden = false;
}
$("#picker-grid").addEventListener("click", async e => {
  const c = e.target.closest(".bcard"); if (!c) return;
  const key = c.dataset.broker;
  $("#picker").hidden = true;
  if (key === "demo") { await api("/api/settings", { method: "POST", body: JSON.stringify({ mode: "demo", picked: true }) }); location.hash = "board"; return; }
  await api("/api/settings", { method: "POST", body: JSON.stringify({ picked: true }) });
  state.selBroker = key;
  if (location.hash === "#settings") loadSettings(); else location.hash = "settings";
});

$("#al-form").addEventListener("submit", async e => {
  e.preventDefault();
  const f = e.target;
  const body = { paper_eod: f.paper_eod.checked, alerts: {
    zone: f.zone.checked, trap: f.trap.checked, top: f.top.checked, paper: f.paper.checked,
    min_score: +f.min_score.value, cooldown_min: +f.cooldown_min.value,
    telegram_token: f.telegram_token.value.trim(), telegram_chat: f.telegram_chat.value.trim() } };
  await api("/api/settings", { method: "POST", body: JSON.stringify(body) });
  toast("Alert settings saved");
  loadSettings();
});
$("#al-sound").addEventListener("change", e => { state.sound = e.target.checked; store.set("sound", state.sound); if (state.sound) beep(1); });
$("#al-desktop").addEventListener("change", async e => {
  if (!e.target.checked) { state.desktop = false; store.set("desktop", false); return; }
  if (!("Notification" in window)) { toast("This browser doesn't support desktop notifications"); e.target.checked = false; return; }
  const perm = await Notification.requestPermission();
  state.desktop = perm === "granted";
  store.set("desktop", state.desktop);
  e.target.checked = state.desktop;
  toast(state.desktop ? "Desktop alerts on" : "Notifications were blocked by the browser");
});
$("#al-test").addEventListener("click", async () => {
  await api("/api/alerts/test", { method: "POST" });
  setTimeout(async () => {
    fetchAlerts();
    const d = await api("/api/alerts?since=999999");
    if (d.telegram_status) $("#tg-status").textContent = "Telegram: " + d.telegram_status;
  }, 1500);
});
/* ================================================================ boot */
route();
pollStatus();
fetchAlerts(true);
})();
