/* Pharma Spatial — static front end. Data: web/data/*.json from pipeline/export_web.py */
(() => {
"use strict";

const D = "data/";
const NON_CONUS_SKIP = new Set(["72", "78", "66", "60", "69"]);   // PR + territories: not drawn by AlbersUsa
const PREV_BY_CLASS = {           // PLACES measure shown as "disease prevalence" for each class
  T2_BIOLOGIC: ["asthma", "Current asthma, adults %"],
  DOAC: ["chd", "Coronary heart disease, adults % (AF not in PLACES)"],
  SGLT2: ["diabetes", "Diagnosed diabetes, adults %"],
  BASAL_INSULIN: ["diabetes", "Diagnosed diabetes, adults %"],
};

const fmt = {
  int: d3.format(",.0f"),
  usd: v => v == null ? "–" : v >= 1e9 ? "$" + d3.format(".2f")(v / 1e9) + "B"
          : v >= 1e6 ? "$" + d3.format(".1f")(v / 1e6) + "M"
          : v >= 1e3 ? "$" + d3.format(".0f")(v / 1e3) + "K" : "$" + d3.format(",.0f")(v),
  x: v => v == null ? "–" : d3.format(".2f")(v) + "×",
  pct: v => v == null ? "–" : d3.format(".1%")(v),
  num: v => v == null ? "–" : d3.format(",.1f")(v),
  big: v => v == null ? "–" : v >= 1e6 ? d3.format(".2s")(v).replace("G", "B") : d3.format(",.0f")(v),
};
const nz = v => v == null ? "–" : v;
const pctTick = v => { const p = v * 100; return (p >= 1 ? d3.format(".0f")(p) : String(+p.toPrecision(1))) + "%"; };
function countyName(fips) {
  const g = S.geo && S.geo.features.find(x => x.id === fips);
  if (g) return `${g.properties.name}, ${g.properties.state}`;
  const i = S.baseIdx.get(fips);
  return i == null ? fips : S.base.name[i].replace(/ Municipio, Puerto Rico$/, ", PR");
}

const S = { meta: null, base: null, baseIdx: new Map(), geo: null, states: null,
            prod: new Map(), cur: null, metric: "per1k65", sel: null, hover: null };

// ---------------------------------------------------------------- metrics
const METRICS = {
  per1k65: { label: "Fills per 1,000 aged 65+", kind: "seq", get: (p, i) => p.per1k65[i], fmt: fmt.num,
             help: "Part D 30-day-equivalent fills per 1,000 residents aged 65+." },
  pay:     { label: "Promotion $ (Open Payments)", kind: "log", get: (p, i) => p.pay[i], fmt: fmt.usd,
             help: "General payments to HCPs in the county that name this product (meals, speaker fees, travel, consulting)." },
  tvs:     { label: "Promotion vs sales (smoothed)", kind: "div", get: (p, i) => p.tvs[i], fmt: fmt.x, gated: true,
             help: "County share of national payments ÷ share of national fills, smoothed toward 1. Red: promoted more than sales predict. Blue: less." },
  tvb:     { label: "Promotion vs disease burden", kind: "div", get: (p, i) => p.tvb[i], fmt: fmt.x, gated: true,
             help: "County share of national payments ÷ share of national disease burden (PLACES prevalence × population, or 65+ population)." },
  share:   { label: "Share of class", kind: "seq01", get: (p, i) => p.share[i], fmt: fmt.pct,
             help: "This product's share of 30-day fills within its competitive class, in the county." },
  prev:    { label: "Disease prevalence (CDC PLACES)", kind: "seq", base: true, fmt: v => v == null ? "–" : v.toFixed(1) + "%",
             help: "" },
  svi:     { label: "Social Vulnerability Index", kind: "seq01", base: true, fmt: v => v == null ? "–" : v.toFixed(2),
             help: "CDC/ATSDR SVI 2022 overall percentile (0 = least, 1 = most vulnerable)." },
};

// ---------------------------------------------------------------- load
Promise.all([
  d3.json(D + "meta.json"), d3.json(D + "county_base.json"),
  d3.json(D + "counties.geojson"), d3.json(D + "states.geojson"),
  d3.json(D + "indications.json"), d3.json(D + "companies.json"),
]).then(([meta, base, geo, states, ind, cos]) => {
  S.meta = meta; S.base = base;
  base.fips.forEach((f, i) => S.baseIdx.set(f, i));
  geo.features = geo.features.filter(f => !NON_CONUS_SKIP.has(f.id.slice(0, 2)));
  states.features = states.features.filter(f => !NON_CONUS_SKIP.has(f.id));
  S.geo = geo; S.states = states;
  d3.select("#year").text(meta.year);
  initTabs(); initControls(); initMap(); renderAbout(); renderCompanies(cos); renderIndications(ind);
  selectProduct(meta.products.find(p => p.id === "DUPIXENT") ? "DUPIXENT" : meta.products[0].id);
}).catch(err => {
  d3.select("main").insert("p", ":first-child").attr("class", "card")
    .text("Could not load data/ — run `python -m pipeline.export_web` and serve web/ over HTTP. (" + err + ")");
});

function initTabs() {
  d3.selectAll(".tabs button").on("click", function () {
    const t = this.dataset.tab;
    d3.selectAll(".tabs button").attr("aria-selected", function () { return this.dataset.tab === t; });
    d3.selectAll(".tab").classed("on", function () { return this.id === "tab-" + t; });
  });
}

function initControls() {
  const sel = d3.select("#product");
  const byClass = d3.group(S.meta.products, p => p.class_name);
  for (const [cls, ps] of byClass) {
    const g = sel.append("optgroup").attr("label", cls);
    for (const p of ps) g.append("option").attr("value", p.id).text(`${p.brand} (${p.generic}) — ${p.company}`);
  }
  sel.on("change", e => selectProduct(e.target.value));
  d3.select("#metric").selectAll("option").data(Object.entries(METRICS)).join("option")
    .attr("value", d => d[0]).text(d => d[1].label);
  d3.select("#metric").property("value", S.metric).on("change", e => { S.metric = e.target.value; paint(); });
  d3.select("#evidence").on("change", paint);
}

async function selectProduct(id) {
  d3.select("#product").property("value", id);
  if (!S.prod.has(id)) {
    const p = await d3.json(D + "product/" + id + ".json");
    p.idx = new Map(p.fips.map((f, i) => [f, i]));
    S.prod.set(id, p);
  }
  S.cur = S.meta.products.find(p => p.id === id);
  S.cur.data = S.prod.get(id);
  renderKpis(); paint(); renderScatter(); renderTop(); renderDetail();
}

// ---------------------------------------------------------------- values
function value(fips, key = S.metric) {
  const m = METRICS[key];
  if (m.base) {
    const i = S.baseIdx.get(fips);
    if (i == null) return null;
    if (key === "svi") return S.base.svi[i];
    const [col] = PREV_BY_CLASS[S.cur.class] || ["diabetes"];
    return S.base[col][i];
  }
  const p = S.cur.data, i = p.idx.get(fips);
  if (i == null) return null;
  if (m.gated && d3.select("#evidence").property("checked") && !p.ok[i]) return null;
  return m.get(p, i);
}

function scaleFor(key) {
  const m = METRICS[key];
  const vals = S.geo.features.map(f => value(f.id, key)).filter(v => v != null && isFinite(v));
  if (m.kind === "div") {
    return { s: d3.scaleDivergingLog(t => d3.interpolateRdBu(1 - t)).domain([1 / 4, 1, 4]).clamp(true), kind: "div" };
  }
  if (m.kind === "log") {
    const pos = vals.filter(v => v > 0);
    const hi = d3.quantile(pos.sort(d3.ascending), 0.98) || 1;
    return { s: d3.scaleSequentialLog(d3.interpolateViridis).domain([Math.max(1, d3.min(pos) || 1), hi]).clamp(true), kind: "log" };
  }
  if (m.kind === "seq01") return { s: d3.scaleSequential(d3.interpolateViridis).domain([0, 1]), kind: "seq" };
  vals.sort(d3.ascending);
  return { s: d3.scaleSequential(d3.interpolateViridis)
             .domain([d3.quantile(vals, 0.02) ?? 0, d3.quantile(vals, 0.98) ?? 1]).clamp(true), kind: "seq" };
}

// ---------------------------------------------------------------- map
let path, mapG;
function initMap() {
  const W = 975, H = 610;
  const svg = d3.select("#map").attr("viewBox", [0, 0, W, H]);
  const proj = d3.geoAlbersUsa().fitSize([W, H], S.geo);
  path = d3.geoPath(proj);
  mapG = svg.append("g");
  mapG.selectAll("path.c").data(S.geo.features, d => d.id).join("path")
    .attr("class", "c").attr("d", path)
    .on("mousemove", (e, d) => showTip(e, d))
    .on("mouseleave", hideTip)
    .on("click", (e, d) => select(d.id));
  svg.append("g").selectAll("path.st").data(S.states.features).join("path").attr("class", "st").attr("d", path);
  svg.call(d3.zoom().scaleExtent([1, 8]).translateExtent([[0, 0], [W, H]])
    .on("zoom", e => svg.selectAll("g").attr("transform", e.transform)));
}

function paint() {
  if (!S.cur) return;
  const m = METRICS[S.metric], { s, kind } = scaleFor(S.metric);
  const nodata = getComputedStyle(document.documentElement).getPropertyValue("--nodata").trim();
  mapG.selectAll("path.c").attr("fill", d => {
    const v = value(d.id);
    if (v == null || !isFinite(v)) return nodata;
    if (kind === "log" && v <= 0) return nodata;
    return s(v);
  });
  let help = m.help;
  if (S.metric === "prev") help = "County crude prevalence: " + (PREV_BY_CLASS[S.cur.class] || ["", "diabetes"])[1] + ".";
  if (m.gated && d3.select("#evidence").property("checked")) help += " Grey: fewer than 10 payments or 120 fills (too thin to read).";
  d3.select("#metric-help").text(help);
  legend(s, kind, m);
}

function legend(s, kind, m) {
  const W = 320, H = 44, box = d3.select("#legend").html("");
  box.append("div").text(m.label);
  const svg = box.append("svg").attr("viewBox", [0, 0, W, H]).attr("width", W);
  const id = "lg" + Math.random().toString(36).slice(2);
  const grad = svg.append("defs").append("linearGradient").attr("id", id);
  const [a, b] = [s.domain()[0], s.domain()[s.domain().length - 1]];
  const x = (kind === "div" || kind === "log" ? d3.scaleLog() : d3.scaleLinear()).domain([a, b]).range([20, W - 20]);
  d3.range(0, 1.0001, 0.05).forEach(t => grad.append("stop").attr("offset", t).attr("stop-color", s(x.invert(20 + t * (W - 40)))));
  svg.append("rect").attr("x", 20).attr("y", 4).attr("width", W - 40).attr("height", 12).attr("rx", 2).attr("fill", `url(#${id})`);
  const ticks = kind === "div" ? [0.25, 0.5, 1, 2, 4] : x.ticks(kind === "log" ? 4 : 5);
  svg.append("g").attr("transform", "translate(0,20)").selectAll("text").data(ticks).join("text")
    .attr("x", x).attr("y", 12).attr("text-anchor", "middle")
    .text(t => kind === "div" ? t + "×" : m.fmt(t));
}

function showTip(e, d) {
  const box = d3.select(".map-box").node().getBoundingClientRect();
  const v = value(d.id);
  d3.select("#tip").attr("hidden", null)
    .style("left", Math.min(e.clientX - box.left + 12, box.width - 250) + "px")
    .style("top", (e.clientY - box.top + 12) + "px")
    .html(`<b>${d.properties.name}, ${d.properties.state}</b>${METRICS[S.metric].label}: ${METRICS[S.metric].fmt(v)}`);
  highlightDot(d.id);
}
function hideTip() { d3.select("#tip").attr("hidden", true); highlightDot(S.sel); }

function select(fips) {
  S.sel = fips;
  mapG.selectAll("path.c").classed("sel", d => d.id === fips);
  highlightDot(fips); renderDetail();
}

// ---------------------------------------------------------------- panels
function renderKpis() {
  const p = S.cur;
  const items = [
    ["30-day fills", fmt.big(p.fills_30d)], ["Gross cost (pre-rebate)", fmt.usd(p.gross_cost)],
    ["Promotion $", fmt.usd(p.pay_usd)],
    ["Promotion per fill", p.fills_30d ? "$" + (p.pay_usd / p.fills_30d).toFixed(2) : "–"],
    ["Counties with activity", fmt.int(p.n_counties || 0)],
  ];
  d3.select("#kpis").selectAll(".kpi").data(items).join("div").attr("class", "kpi")
    .html(d => `<div class="v">${d[1]}</div><div class="k">${d[0]}</div>`);
}

function renderDetail() {
  const el = d3.select("#detail");
  if (!S.sel) { el.html('<p class="muted">Click a county for details.</p>'); return; }
  const f = S.geo.features.find(g => g.id === S.sel);
  const p = S.cur.data, i = p.idx.get(S.sel), bi = S.baseIdx.get(S.sel);
  const b = k => bi == null ? null : S.base[k][bi];
  const r = k => i == null ? null : p[k][i];
  const [prevCol, prevLbl] = PREV_BY_CLASS[S.cur.class] || ["diabetes", "Diabetes %"];
  el.html(`<h3>${countyName(S.sel)}</h3>
    <div class="muted">FIPS ${S.sel} · ${S.cur.brand}</div>
    <dl>
      <dt>30-day fills</dt><dd>${r("fills") == null ? "–" : fmt.int(r("fills"))}</dd>
      <dt>Fills / 1k 65+</dt><dd>${fmt.num(r("per1k65"))}</dd>
      <dt>Gross cost</dt><dd>${fmt.usd(r("cost"))}</dd>
      <dt>Prescribers</dt><dd>${nz(r("n_hcp"))}</dd>
      <dt>Promotion $</dt><dd>${fmt.usd(r("pay"))}</dd>
      <dt>Payments</dt><dd>${r("n_pay") == null ? "–" : fmt.int(r("n_pay"))}</dd>
      <dt>Promo vs sales</dt><dd>${fmt.x(r("tvs"))}${i != null && !p.ok[i] ? " *" : ""}</dd>
      <dt>Promo vs burden</dt><dd>${fmt.x(r("tvb"))}</dd>
      <dt>Share of class</dt><dd>${fmt.pct(r("share"))}</dd>
    </dl>
    <dl>
      <dt>Population</dt><dd>${b("pop") == null ? "–" : fmt.int(b("pop"))}</dd>
      <dt>Aged 65+</dt><dd>${b("pop65") == null ? "–" : fmt.int(b("pop65"))}</dd>
      <dt>Median income</dt><dd>${b("income") == null ? "–" : "$" + fmt.int(b("income"))}</dd>
      <dt>SVI</dt><dd>${b("svi") == null ? "–" : b("svi").toFixed(2)}</dd>
      <dt>${prevLbl.split(",")[0]}</dt><dd>${b(prevCol) == null ? "–" : b(prevCol).toFixed(1) + "%"}</dd>
    </dl>
    ${i != null && !p.ok[i] ? '<p class="muted">* thin cell: fewer than 10 payments or 120 fills.</p>' : ""}`);
}

// ---------------------------------------------------------------- scatter
let scat;
function renderScatter() {
  const W = 560, H = 380, m = { t: 10, r: 14, b: 40, l: 58 };
  const p = S.cur.data;
  const totPay = d3.sum(p.pay), totFill = d3.sum(p.fills);
  const pts = p.fips.map((f, i) => ({ f, x: p.fills[i] / totFill, y: p.pay[i] / totPay, ok: p.ok[i] }))
    .filter(d => d.x > 0 && d.y > 0);
  const svg = d3.select("#scatter").attr("viewBox", [0, 0, W, H]).html("");
  if (!pts.length) { svg.append("text").attr("x", 20).attr("y", 30).attr("class", "lbl").text("No counties with both fills and payments."); return; }
  const ext = d3.extent(pts.flatMap(d => [d.x, d.y]));
  const x = d3.scaleLog().domain(ext).range([m.l, W - m.r]).nice();
  const y = d3.scaleLog().domain(ext).range([H - m.b, m.t]).nice();
  const pf = pctTick;
  const decades = sc => sc.ticks().filter(t => Math.abs(Math.log10(t) - Math.round(Math.log10(t))) < 1e-9);
  svg.append("g").attr("class", "axis").attr("transform", `translate(0,${H - m.b})`).call(d3.axisBottom(x).tickValues(decades(x)).tickFormat(pf));
  svg.append("g").attr("class", "axis").attr("transform", `translate(${m.l},0)`).call(d3.axisLeft(y).tickValues(decades(y)).tickFormat(pf));
  svg.append("text").attr("class", "lbl").attr("x", W - m.r).attr("y", H - 6).attr("text-anchor", "end").text("share of national fills →");
  svg.append("text").attr("class", "lbl").attr("x", m.l + 6).attr("y", m.t + 10).text("↑ share of payments");
  const [lo, hi] = x.domain();
  svg.append("line").attr("class", "diag").attr("x1", x(lo)).attr("y1", y(lo)).attr("x2", x(hi)).attr("y2", y(hi));
  scat = svg.append("g").selectAll("circle").data(pts, d => d.f).join("circle")
    .attr("class", "dot").attr("cx", d => x(d.x)).attr("cy", d => y(d.y)).attr("r", d => d.ok ? 3 : 2)
    .style("cursor", "pointer").on("click", (e, d) => select(d.f))
    .on("mouseenter", function (e, d) {
      d3.select(this).classed("hl", true);
      d3.select(this).append("title").text(`${countyName(d.f)}\nfills ${d3.format(".3~%")(d.x)} · payments ${d3.format(".3~%")(d.y)}`);
    })
    .on("mouseleave", function () { d3.select(this).classed("hl", d => d.f === S.sel).select("title").remove(); });
  highlightDot(S.sel);
}
function highlightDot(fips) {
  if (!scat) return;
  scat.classed("hl", d => d.f === fips || d.f === S.sel);
  scat.filter(d => d.f === fips || d.f === S.sel).raise();
}

function renderTop() {
  const p = S.cur.data;
  const rows = p.fips.map((f, i) => ({ f, tvs: p.tvs[i], pay: p.pay[i], fills: p.fills[i], ok: p.ok[i] }))
    .filter(d => d.ok && d.tvs != null).sort((a, b) => b.tvs - a.tvs).slice(0, 12);
  const t = d3.select("#top").html("");
  t.append("thead").append("tr").selectAll("th").data(["County", "Promo vs sales", "Promotion $", "Fills"]).join("th").text(d => d);
  t.append("tbody").selectAll("tr").data(rows).join("tr").attr("class", "click").on("click", (e, d) => select(d.f))
    .html(d => `<td>${countyName(d.f)}</td><td>${fmt.x(d.tvs)}</td><td>${fmt.usd(d.pay)}</td><td>${fmt.int(d.fills)}</td>`);
  if (!rows.length) t.append("caption").attr("class", "muted").text("No county has enough evidence for this product.");
}

// ---------------------------------------------------------------- companies
function renderCompanies(cos) {
  const W = 1000, H = 520, m = { t: 14, r: 20, b: 44, l: 64 };
  const pts = cos.filter(d => d.gross_cost > 0 && d.promo_drug > 0);
  const x = d3.scaleLog().domain(d3.extent(pts, d => d.gross_cost)).nice().range([m.l, W - m.r]);
  const y = d3.scaleLog().domain(d3.extent(pts, d => d.promo_drug)).nice().range([H - m.b, m.t]);
  const r = d3.scaleSqrt().domain([0, d3.max(pts, d => d.n_hcps)]).range([2, 18]);
  const svg = d3.select("#co-scatter").attr("viewBox", [0, 0, W, H]);
  svg.append("g").attr("class", "axis").attr("transform", `translate(0,${H - m.b})`).call(d3.axisBottom(x).ticks(6, fmt.usd));
  svg.append("g").attr("class", "axis").attr("transform", `translate(${m.l},0)`).call(d3.axisLeft(y).ticks(6, fmt.usd));
  svg.append("text").attr("class", "lbl").attr("x", W - m.r).attr("y", H - 8).attr("text-anchor", "end").text("Medicare Part D + B gross cost →");
  svg.append("text").attr("class", "lbl").attr("x", m.l + 4).attr("y", m.t + 4).text("↑ drug-attributed promotion $");
  const g = svg.append("g").selectAll("g").data(pts).join("g").attr("transform", d => `translate(${x(d.gross_cost)},${y(d.promo_drug)})`);
  g.append("circle").attr("class", "dot").attr("r", d => r(d.n_hcps))
    .append("title").text(d => `${d.parent}\nsales ${fmt.usd(d.gross_cost)} · drug promo ${fmt.usd(d.promo_drug)} · HCPs paid ${fmt.int(d.n_hcps)}`);
  const top = new Set([...pts].sort((a, b) => b.gross_cost - a.gross_cost).slice(0, 8).map(d => d.parent)
    .concat([...pts].sort((a, b) => b.promo_drug - a.promo_drug).slice(0, 5).map(d => d.parent)));
  g.filter(d => top.has(d.parent)).append("text").attr("class", "lbl").attr("x", d => r(d.n_hcps) + 3).attr("y", 4).text(d => d.parent);

  const cols = [["parent", "Parent company", String], ["gross_cost", "Part D+B gross", fmt.usd],
    ["promo_drug", "Drug promotion", fmt.usd], ["promo_all", "All payments", fmt.usd],
    ["per", "Promo per $1k sales", v => v == null ? "–" : "$" + v.toFixed(2)],
    ["n_hcps", "HCPs paid", v => v == null ? "–" : fmt.int(v)], ["n_entities", "Paying entities", v => nz(v)]];
  const rows = cos.map(d => ({ ...d, per: d.gross_cost > 0 && d.promo_drug != null ? d.promo_drug / d.gross_cost * 1000 : null }));
  let key = "gross_cost", dir = -1, all = false;
  const t = d3.select("#co-table");
  const more = d3.select(t.node().parentNode).append("button").attr("class", "more")
    .on("click", () => { all = !all; draw(); });
  const draw = () => {
    rows.sort((a, b) => dir * (((a[key] ?? -Infinity) > (b[key] ?? -Infinity)) - ((a[key] ?? -Infinity) < (b[key] ?? -Infinity))));
    t.html("");
    t.append("thead").append("tr").selectAll("th").data(cols).join("th")
      .text(c => c[1] + (c[0] === key ? (dir < 0 ? " ↓" : " ↑") : ""))
      .on("click", (e, c) => { dir = c[0] === key ? -dir : -1; key = c[0]; draw(); });
    t.append("tbody").selectAll("tr").data(all ? rows : rows.slice(0, 40)).join("tr")
      .html(d => cols.map(c => `<td>${c[2](d[c[0]])}</td>`).join(""));
    more.text(all ? "Show top 40" : `Show all ${rows.length}`);
  };
  draw();
}

// ---------------------------------------------------------------- indications
function renderIndications(ind) {
  const pal = d3.scaleOrdinal([...d3.schemeTableau10, "#17becf", "#8c564b", "#bcbd22"]);
  const draw = () => {
    const k = d3.select("#ind-measure").property("value");
    const byP = d3.group(ind, d => d.product);
    const box = d3.select("#ind-chart").html("");
    const inds = [...new Set(ind.map(d => d.indication))];
    box.append("div").attr("class", "ind-legend").selectAll("span").data(inds).join("span")
      .html(d => `<i style="background:${pal(d)}"></i>${d.replace(/_/g, " ")}`);
    const fmtK = k === "fills_30d" ? fmt.big : fmt.usd;
    for (const p of S.meta.products) {
      const rows = (byP.get(p.id) || []).filter(d => d[k] > 0);
      const tot = d3.sum(rows, d => d[k]);
      const row = box.append("div").attr("class", "ind-row");
      row.append("div").text(p.brand);
      const bar = row.append("div").attr("class", "ind-bar");
      bar.selectAll("span").data(rows).join("span")
        .style("width", d => (100 * d[k] / (tot || 1)) + "%").style("background", d => pal(d.indication))
        .attr("title", d => `${d.indication.replace(/_/g, " ")}: ${fmtK(d[k])} (${fmt.pct(d[k] / tot)})`);
      row.append("div").attr("class", "ind-total").text(tot ? fmtK(tot) : "–");
    }
  };
  d3.select("#ind-measure").on("change", draw);
  // products list comes from meta; wait until it's set
  const wait = () => S.meta ? draw() : setTimeout(wait, 50);
  wait();
}

// ---------------------------------------------------------------- about
function renderAbout() {
  const t = d3.select("#qa");
  t.append("tbody").selectAll("tr").data(S.meta.qa).join("tr")
    .html(d => `<td>${d.check}</td><td>${d.value == null ? "–" : fmt.num(d.value)}</td>`);
  d3.select("#built").text(`Data year ${S.meta.year} · built ${S.meta.built_at}`);
}
})();
