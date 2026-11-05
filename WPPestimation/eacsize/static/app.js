"use strict";
// eacsize UI: form from the model schema, run on the server, show the latest and previous runs.

const SECTIONS = ["inputs", "mission", "requirements", "assumptions"];
const S = { schema: null, presets: {}, defaults: {}, meta: {}, runs: [], current: null, selected: new Set() };
const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

// ---------------------------------------------------------------------------------------
// formatting and labels
// ---------------------------------------------------------------------------------------
function fmt(v, d = 1) {
  if (v === null || v === undefined || Number.isNaN(v)) return "–";
  if (typeof v === "boolean") return v ? "yes" : "no";
  if (typeof v !== "number") return esc(v);
  if (Math.abs(v) >= 1000) return v.toLocaleString("en-US", { maximumFractionDigits: 0 });
  return v.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
}
const L = {
  // summary
  mtom_kg: ["MTOM", "kg", 1], oew_excl_battery_kg: ["OEW excl. battery", "kg", 1], payload_kg: ["Payload", "kg", 0],
  battery_kg: ["Battery mass", "kg", 1], battery_kwh: ["Battery energy (nominal)", "kWh", 1],
  battery_mass_frac: ["Battery mass fraction", "-", 3], empty_frac_incl_battery: ["(OEW + battery) / MTOM", "-", 3],
  wing_area_m2: ["Wing area", "m²", 2], span_m: ["Span", "m", 2], aspect_ratio: ["Aspect ratio", "-", 2],
  wing_loading_kgm2: ["Wing loading", "kg/m²", 1], installed_power_kw: ["Installed shaft power", "kW", 1],
  power_loading_wkg: ["Power loading", "W/kg", 1], cd0: ["C_D0", "-", 4], oswald_e: ["Oswald e", "-", 3],
  ld_max: ["L/D max", "-", 1], range_cruise_km: ["Range at cruise speed", "km", 0],
  range_cruise_eol_km: ["Range at cruise speed, end of life", "km", 0], endurance_vmp_h: ["Endurance at V_mp", "h", 2],
  vs0_kcas: ["Stall speed V_S0", "KCAS", 1], takeoff_15m_m: ["Take-off over 15 m", "m", 0],
  roc_sl_cont_ms: ["Rate of climb SL, continuous", "m/s", 2],
  // performance
  vs1_kcas: ["Stall speed V_S1 (clean)", "KCAS", 1], vs0_kmh: ["V_S0", "km/h", 0], vs1_kmh: ["V_S1", "km/h", 0],
  v_md_kmh: ["V_md (best range) at cruise alt.", "km/h", 0], v_mp_kmh: ["V_mp (best endurance) at cruise alt.", "km/h", 0],
  v_max_sl_full_kmh: ["Max level speed SL, full power", "km/h", 0], vh_kcas: ["V_H (SL, max continuous)", "KCAS", 0],
  v_max_cruise_alt_cont_kmh: ["Max speed at cruise alt., continuous", "km/h", 0],
  roc_sl_full_ms: ["Rate of climb SL, full power", "m/s", 2], roc_sl_cont_fpm: ["Rate of climb SL, continuous", "fpm", 0],
  service_ceiling_m: ["Service ceiling (capped 12 km)", "m", 0], takeoff_ground_roll_m: ["Take-off ground roll", "m", 0],
  landing_ground_roll_m: ["Landing ground roll", "m", 0], landing_15m_m: ["Landing over 15 m", "m", 0],
  p_batt_cruise_kw: ["Battery power at cruise speed", "kW", 1], energy_per_km_wh: ["Energy per km at cruise", "Wh/km", 0],
  c_rate_takeoff: ["C-rate at take-off", "1/h", 2], c_rate_climb: ["C-rate in climb", "1/h", 2],
  pack_w_per_kg_takeoff: ["Pack specific power at take-off", "W/kg", 0], battery_volume_l: ["Battery volume", "L", 0],
  // mission
  e_nom_kwh: ["Nominal energy", "kWh", 1], e_usable_bol_kwh: ["Usable energy, beginning of life", "kWh", 1],
  e_usable_eol_kwh: ["Usable energy, end of life", "kWh", 1], e_overhead_kwh: ["Sortie overhead (taxi, T/O, climb, descent)", "kWh", 2],
  e_reserve_kwh: ["Reserve energy", "kWh", 2], climb_roc_ms: ["Mission climb rate", "m/s", 2], climb_time_min: ["Climb time", "min", 1],
  climb_dist_km: ["Climb distance", "km", 1], descent_dist_km: ["Descent distance", "km", 1],
  range_cruise_no_reserve_km: ["Range at cruise speed, no reserve", "km", 0], time_cruise_h: ["Airborne time at cruise speed", "h", 2],
  range_vmd_km: ["Range at V_md", "km", 0], endurance_vmp_eol_h: ["Endurance at V_mp, end of life", "h", 2],
  endurance_vmp_no_reserve_h: ["Endurance at V_mp, no reserve", "h", 2], range_ideal_km: ["Ideal range (no overhead, no reserve)", "km", 0],
  endurance_ideal_h: ["Ideal endurance", "h", 2],
};
const lab = (k) => (L[k] ? L[k][0] : k.replace(/_/g, " "));
const unit = (k) => (L[k] ? L[k][1] : "");
const dec = (k) => (L[k] ? L[k][2] : 2);

// ---------------------------------------------------------------------------------------
// API
// ---------------------------------------------------------------------------------------
async function api(path, opts) {
  const r = await fetch(path, opts);
  const j = await r.json();
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}

// ---------------------------------------------------------------------------------------
// form
// ---------------------------------------------------------------------------------------
const SEC_TITLES = { inputs: "", mission: "Mission", requirements: "Requirements", assumptions: "" };
const OPEN_GROUPS = new Set(["Design", "Payload", "Configuration", "Mission", "Battery"]);

function buildForm() {
  const form = $("#form");
  form.innerHTML = "";
  for (const sec of SECTIONS) {
    const groups = {};
    for (const f of S.schema[sec]) {
      const g = f.group || SEC_TITLES[sec] || sec;
      (groups[g] = groups[g] || []).push(f);
      S.defaults[`${sec}.${f.key}`] = f.default;
      S.meta[`${sec}.${f.key}`] = f;
    }
    for (const [g, list] of Object.entries(groups)) {
      const d = document.createElement("details");
      d.className = "grp";
      d.open = OPEN_GROUPS.has(g);
      const nA = list.filter((f) => f.assumption).length;
      d.innerHTML = `<summary><span>${esc(sec === "assumptions" ? "Assumptions · " + g : g)}</span>` +
        `<span class="cnt">${nA ? nA + " A" : ""}</span></summary><div class="fields"></div>`;
      const box = d.querySelector(".fields");
      for (const f of list) box.appendChild(fieldRow(sec, f));
      form.appendChild(d);
    }
  }
  form.addEventListener("input", onFormChange);
  form.addEventListener("change", onFormChange);
}

function fieldRow(sec, f) {
  const id = `${sec}.${f.key}`;
  const row = document.createElement("div");
  row.className = "f";
  row.dataset.id = id;
  const tip = [f.label, f.lo !== null && f.lo !== undefined ? `Range: ${f.lo} – ${f.hi} ${f.unit || ""}` : "",
    `Default: ${f.default}`, f.source ? `Source: ${f.source}` : "", f.sens ? `Sensitivity: ${f.sens}` : "", f.help || ""]
    .filter(Boolean).join("\n");
  let ctl;
  if (f.type === "select") {
    ctl = `<select id="${id}">${f.options.map((o) => `<option value="${esc(o)}">${esc(o)}</option>`).join("")}</select>`;
  } else if (f.type === "bool") {
    ctl = `<input type="checkbox" id="${id}">`;
  } else if (f.type === "text") {
    ctl = `<input type="text" id="${id}">`;
  } else {
    const step = f.type === "int" ? "1" : "any";
    ctl = `<input type="number" step="${step}" id="${id}"${f.lo !== null && f.lo !== undefined ? ` min="${f.type === "int" ? f.lo : 0}"` : ""}>`;
  }
  row.innerHTML = `<label for="${id}" title="${esc(tip)}">${f.assumption ? '<span class="tag">A</span>' : ""}` +
    `<span class="t">${esc(f.label)}</span></label>${ctl}<span class="u" title="${esc(f.unit || "")}">${esc(f.unit || "")}</span>`;
  return row;
}

function getValues() {
  const out = {};
  for (const sec of SECTIONS) {
    out[sec] = {};
    for (const f of S.schema[sec]) {
      const el = document.getElementById(`${sec}.${f.key}`);
      let v;
      if (f.type === "bool") v = el.checked;
      else if (f.type === "select" || f.type === "text") v = el.value;
      else v = el.value === "" ? f.default : Number(el.value);
      out[sec][f.key] = v;
    }
  }
  return out;
}

function setValues(vals) {
  for (const sec of SECTIONS) {
    for (const f of S.schema[sec]) {
      const el = document.getElementById(`${sec}.${f.key}`);
      const v = vals?.[sec]?.[f.key] ?? f.default;
      if (f.type === "bool") el.checked = !!v;
      else el.value = v;
    }
  }
  onFormChange();
}

function applyPreset(name) {
  const base = {};
  if (name && S.presets[name]) {
    for (const sec of SECTIONS) base[sec] = { ...(S.presets[name][sec] || {}) };
    $("#label").value = S.presets[name].title || name;
  } else {
    $("#label").value = "";
  }
  setValues(base);
}

function onFormChange() {
  const v = getValues();
  const i = v.inputs, a = v.assumptions;
  const muted = new Set();
  const m = (sec, keys) => keys.forEach((k) => muted.add(`${sec}.${k}`));
  if (i.mode !== "fixed_mtom") m("inputs", ["mtom_kg"]);
  if (i.mode !== "solve_mtom") m("inputs", ["target", "target_range_km", "target_endurance_h", "size_at_eol"]);
  else m("inputs", [i.target === "range" ? "target_endurance_h" : "target_range_km"]);
  if (i.geometry_mode !== "fixed") m("inputs", ["wing_area_m2", "span_m"]);
  if (i.geometry_mode !== "constraint") { m("inputs", ["wing_loading_kgm2"]); m("assumptions", ["aspect_ratio"]); }
  if (i.payload_override_kg > 0) m("inputs", ["occupant_kg", "baggage_per_seat_kg"]);
  if (i.gear !== "fixed") m("assumptions", ["gear_dq_m2"]);
  if (i.gear !== "retractable") m("assumptions", ["retract_gear_factor"]);
  if (a.e_method !== "fixed") m("assumptions", ["oswald_e"]);
  if (a.cd0_override > 0 || a.ld_override > 0)
    m("assumptions", ["lam_frac_wing", "lam_frac_tail", "lam_frac_fus", "misc_drag_frac", "calibrate_cd0", "gear_dq_m2"]);
  const CLASS2 = ["n_ult", "n_land_ult", "kc_wing", "kc_tail", "kc_fus", "kc_gear", "retract_gear_factor",
    "avionics_uninst_kg", "seat_kg", "brs_kg", "main_gear_len_m", "nose_gear_len_m"];
  if (a.weight_method === "risen_L1" || a.weight_method === "electric_regression") m("assumptions", CLASS2);
  if (["raymer_raw", "roskam_raw", "electric_regression"].includes(a.weight_method)) m("assumptions", ["ref_struct_kg", "ref_systems_kg"]);
  if (a.weight_method !== "electric_regression")
    m("assumptions", ["regression_use_seats", "regression_exclude", "regression_add", "regression_estimate_battery", "regression_pack_wh_kg", "regression_fixed_exponent"]);
  if (i.oew_override_kg > 0)
    m("assumptions", ["weight_method", "n_ult", "n_land_ult", "kc_wing", "kc_tail", "kc_fus", "kc_gear", "retract_gear_factor",
      "avionics_uninst_kg", "seat_kg", "brs_kg", "ref_struct_kg", "ref_systems_kg", "pt_kw_per_kg", "prop_mass_coeff"]);
  for (const row of document.querySelectorAll(".f")) {
    const id = row.dataset.id, [sec, key] = id.split(".");
    row.classList.toggle("changed", String(v[sec][key]) !== String(S.defaults[id]));
    row.classList.toggle("muted", muted.has(id));
  }
}

// ---------------------------------------------------------------------------------------
// charts (inline SVG)
// ---------------------------------------------------------------------------------------
const COLORS = ["var(--s1)", "var(--s2)", "var(--s3)", "var(--s4)", "var(--s5)", "var(--s6)"];

function niceStep(range, n) {
  const raw = range / n, mag = Math.pow(10, Math.floor(Math.log10(raw))), r = raw / mag;
  return (r < 1.5 ? 1 : r < 3 ? 2 : r < 7 ? 5 : 10) * mag;
}
function ticks(lo, hi, n = 5) {
  if (!(hi > lo)) hi = lo + 1;
  const st = niceStep(hi - lo, n), out = [];
  for (let t = Math.ceil(lo / st) * st; t <= hi + st * 1e-6; t += st) out.push(+t.toFixed(10));
  return out;
}

function lineChart({ title, xlabel, ylabel, series, vlines = [], points = [], ymin, ymax, note }) {
  const W = 480, H = 290, ml = 52, mr = 14, mt = 12, mb = 40;
  const xs = series.flatMap((s) => s.x), ys = series.flatMap((s) => s.y).filter((v) => v !== null && Number.isFinite(v));
  points.forEach((p) => { xs.push(p.x); ys.push(p.y); });
  vlines.forEach((v) => xs.push(v.x));
  let x0 = Math.min(...xs), x1 = Math.max(...xs);
  let y0 = ymin ?? Math.min(0, ...ys), y1 = ymax ?? Math.max(...ys) * 1.08;
  if (!Number.isFinite(y1) || y1 <= y0) y1 = y0 + 1;
  const xt = ticks(x0, x1), yt = ticks(y0, y1);
  x0 = Math.min(x0, xt[0]); x1 = Math.max(x1, xt[xt.length - 1]); y1 = Math.max(y1, yt[yt.length - 1]);
  const X = (x) => ml + (x - x0) / (x1 - x0) * (W - ml - mr);
  const Y = (y) => H - mb - (Math.min(Math.max(y, y0), y1) - y0) / (y1 - y0) * (H - mt - mb);
  let g = "";
  yt.forEach((t) => { g += `<line class="gr" x1="${ml}" x2="${W - mr}" y1="${Y(t)}" y2="${Y(t)}"/><text x="${ml - 6}" y="${Y(t) + 3.5}" text-anchor="end">${fmtTick(t)}</text>`; });
  xt.forEach((t) => { g += `<line class="gr" y1="${mt}" y2="${H - mb}" x1="${X(t)}" x2="${X(t)}"/><text x="${X(t)}" y="${H - mb + 14}" text-anchor="middle">${fmtTick(t)}</text>`; });
  g += `<line class="ax" x1="${ml}" x2="${W - mr}" y1="${H - mb}" y2="${H - mb}"/><line class="ax" x1="${ml}" x2="${ml}" y1="${mt}" y2="${H - mb}"/>`;
  g += `<text x="${(ml + W - mr) / 2}" y="${H - 6}" text-anchor="middle">${esc(xlabel)}</text>`;
  g += `<text transform="translate(12 ${(mt + H - mb) / 2}) rotate(-90)" text-anchor="middle">${esc(ylabel)}</text>`;
  vlines.forEach((v) => {
    g += `<line x1="${X(v.x)}" x2="${X(v.x)}" y1="${mt}" y2="${H - mb}" stroke="${v.color || "var(--muted)"}" stroke-dasharray="4 3" stroke-width="1.3"/>` +
      `<text x="${X(v.x) + 3}" y="${mt + 10 + (v.dy || 0)}" class="lab">${esc(v.label)}</text>`;
  });
  series.forEach((s, i) => {
    const c = s.color || COLORS[i % COLORS.length];
    let d = "", pen = false;
    s.x.forEach((x, j) => {
      const y = s.y[j];
      if (y === null || !Number.isFinite(y) || (ymax !== undefined && y > ymax * 1.5)) { pen = false; return; }
      d += `${pen ? "L" : "M"}${X(x).toFixed(1)},${Y(y).toFixed(1)}`; pen = true;
    });
    g += `<path d="${d}" fill="none" stroke="${c}" stroke-width="2" ${s.dash ? 'stroke-dasharray="6 4"' : ""}/>`;
  });
  points.forEach((p) => {
    g += `<circle cx="${X(p.x)}" cy="${Y(p.y)}" r="${p.r || 5}" fill="${p.color || "var(--fg)"}" stroke="var(--surface)" stroke-width="2"/>` +
      (p.label ? `<text x="${X(p.x) + 8}" y="${Y(p.y) - 7}" class="lab">${esc(p.label)}</text>` : "");
  });
  const legend = series.map((s, i) => `<span><i style="background:${s.color || COLORS[i % COLORS.length]}"></i>${esc(s.name)}</span>`).join("");
  return `<div class="card chart"><h3>${esc(title)}</h3><div class="legend">${legend}</div>` +
    `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(title)}">${g}</svg>${note ? `<p class="note">${esc(note)}</p>` : ""}</div>`;
}
function fmtTick(t) { return Math.abs(t) >= 1000 ? (t / 1000) + "k" : String(+t.toFixed(4)); }

function constraintChart(c) {
  const names = { climb: "Rate of climb", cruise: "Cruise speed", ceiling: "Service ceiling", takeoff: "Take-off distance", v_max: "Max speed" };
  const series = Object.entries(c.curves).map(([k, y]) => ({ name: names[k] || k, x: c.ws_kgm2, y }));
  const vl = Object.entries(c.limits_kgm2).map(([k, x], i) => ({ x, label: k === "stall" ? "V_S0 limit" : "landing limit", dy: i * 12, color: "var(--bad)" }))
    .filter((v) => v.x >= c.ws_kgm2[0] && v.x <= c.ws_kgm2[c.ws_kgm2.length - 1]);
  const ymax = c.design.pw_wkg * 2;
  return lineChart({
    title: "Constraint diagram", xlabel: "Wing loading W/S [kg/m²]", ylabel: "Shaft power / mass [W/kg]", series, vlines: vl,
    points: [{ x: c.design.ws_kgm2, y: c.design.pw_wkg, label: "design" }], ymin: 0, ymax,
    note: "Feasible region: above every power curve and left of the wing-loading limits.",
  });
}

function massBars(mass, mtom) {
  const cat = (k) => ["wing", "htail", "vtail", "fuselage", "main_gear", "nose_gear", "landing_gear", "structure",
    "oew_override", "airframe_regression"].includes(k) ? 0 :
    ["e_powertrain", "propeller"].includes(k) ? 2 : k.startsWith("battery") ? 3 : k === "payload" ? 4 : 1;
  const col = ["var(--s1)", "var(--s6)", "var(--s4)", "var(--s5)", "var(--s3)"];
  const rows = Object.entries(mass).filter(([, v]) => v > 0.05);
  const max = Math.max(...rows.map(([, v]) => v));
  const html = rows.map(([k, v]) => `<div class="row"><span>${esc(k.replace(/_/g, " "))}</span>` +
    `<div class="bar" style="width:${(v / max * 100).toFixed(1)}%;background:${col[cat(k)]}"></div>` +
    `<span class="n">${fmt(v, 1)} kg · ${fmt(v / mtom * 100, 1)}%</span></div>`).join("");
  const leg = ["Structure", "Systems / equipment", "E-powertrain + propeller", "Battery", "Payload"]
    .map((n, i) => `<span><i style="background:${col[i]}"></i>${n}</span>`).join("");
  return `<div class="card bars"><h3>Mass breakdown at take-off (${fmt(mtom, 1)} kg)</h3><div class="legend">${leg}</div>${html}</div>`;
}

// ---------------------------------------------------------------------------------------
// result rendering
// ---------------------------------------------------------------------------------------
function kv(obj, keys) {
  return `<div class="tbl-wrap"><table><tbody>${(keys || Object.keys(obj)).map((k) =>
    `<tr><td>${esc(lab(k))}</td><td class="n">${fmt(obj[k], dec(k))}</td><td>${esc(unit(k))}</td></tr>`).join("")}</tbody></table></div>`;
}

function renderResult(rec) {
  S.current = rec;
  const r = rec.results, s = r.summary, d = r.design;
  const kpis = ["mtom_kg", "battery_kg", "battery_kwh", "battery_mass_frac", "wing_area_m2", "installed_power_kw", "ld_max",
    "range_cruise_km", "range_cruise_eol_km", "endurance_vmp_h", "vs0_kcas", "takeoff_15m_m"];
  const kpiHtml = kpis.map((k) => `<div class="kpi"><div class="k">${esc(lab(k))}</div><div class="v">${fmt(s[k], dec(k))}<small>${esc(unit(k))}</small></div></div>`).join("");
  const checks = r.checks.map((c) => `<tr><td>${esc(c.name)}</td><td class="n">${fmt(c.value, 2)}</td><td class="n">${fmt(c.limit, 2)}</td>` +
    `<td>${esc(c.unit)}</td><td><span class="pill ${c.severity}">${c.severity}</span></td><td class="note">${esc(c.note)}</td></tr>`).join("");
  const rs = r.charts.range_speed, pr = r.charts.payload_range;
  const charts = [
    constraintChart(r.charts.constraint),
    lineChart({
      title: "Range vs cruise speed (with reserve)", xlabel: "Cruise TAS [km/h]", ylabel: "Range [km]",
      series: [{ name: "Beginning of life", x: rs.v_kmh, y: rs.range_bol_km }, { name: "End of life", x: rs.v_kmh, y: rs.range_eol_km, dash: true }],
      vlines: [{ x: rs.v_cruise_kmh, label: "design cruise" }, { x: r.performance.v_md_kmh, label: "V_md", dy: 12 }],
    }),
    lineChart({
      title: "Battery power required at cruise altitude", xlabel: "TAS [km/h]", ylabel: "Battery power [kW]",
      series: [{ name: "P_battery = D·V / η_chain", x: rs.v_kmh, y: rs.p_batt_kw, color: "var(--s2)" }],
      vlines: [{ x: r.performance.v_mp_kmh, label: "V_mp" }, { x: rs.v_cruise_kmh, label: "design cruise", dy: 12 }],
    }),
    lineChart({
      title: "Payload–range at cruise speed", xlabel: "Payload [kg]", ylabel: "Range with reserve [km]",
      series: [{ name: "This aircraft (fixed pack, lighter TOM)", x: pr.fixed_pack.map((p) => p[0]), y: pr.fixed_pack.map((p) => p[1]) },
        { name: "Pack traded for payload at MTOM", x: pr.pack_swap_at_mtom.map((p) => p[0]), y: pr.pack_swap_at_mtom.map((p) => p[1]), dash: true }],
      vlines: [{ x: pr.design_payload_kg, label: "design payload" }],
    }),
  ].join("");
  const used = diffFromDefaults(rec);
  const usedHtml = used.length ? `<table><thead><tr><th>Parameter</th><th>Value</th><th>Default</th><th></th></tr></thead><tbody>${used.map((u) =>
    `<tr><td>${esc(u.label)}</td><td class="n">${fmt(u.value, 4)}</td><td class="n">${fmt(u.def, 4)}</td><td>${u.a ? '<span class="tag">A</span>' : ""}</td></tr>`).join("")}</tbody></table>`
    : `<p class="note">All inputs and assumptions at their defaults.</p>`;
  const cd0p = Object.entries(d.cd0_parts || {}).map(([k, v]) => `${k} ${fmt(v, 4)}`).join(" · ");
  const pwreq = Object.entries(d.pw_required_wkg).map(([k, v]) => `${k} ${fmt(v, 1)}`).join(" · ");
  const wsl = Object.entries(d.ws_limits_kgm2).map(([k, v]) => `${k} ${fmt(v, 1)}`).join(" · ");
  const geo = d.geometry;
  $("#result").innerHTML = `
    <div class="card">
      <div class="rhead"><h2>${esc(rec.id)} · ${esc(rec.label)}</h2><span class="pill ${r.status}">${r.status}</span>
        <span class="meta">${esc(rec.timestamp)} · ${esc(rec.inputs.mode)} · geometry ${esc(rec.inputs.geometry_mode)} ·
        weights ${esc(rec.assumptions.weight_method)} · ${fmt(rec.assumptions.pack_wh_kg, 0)} Wh/kg · ${r.closure.evaluations} evaluation(s), ${fmt(r.runtime_s, 2)} s</span>
        <button class="small" type="button" id="loadcur">Load these inputs</button>
        <button class="small" type="button" id="hs1cur" data-hs1="${esc(rec.id)}">${hs1Label(rec.id)}</button></div>
      ${r.messages.length ? `<ul class="msgs">${r.messages.map((m) => `<li>${esc(m)}</li>`).join("")}</ul>` : ""}
      <div class="kpis">${kpiHtml}</div>
    </div>
    <div class="card"><h3>Feasibility checks</h3><div class="tbl-wrap"><table><thead><tr><th>Check</th><th>Value</th><th>Limit</th><th></th><th>Result</th><th></th></tr></thead><tbody>${checks}</tbody></table></div></div>
    <div class="grid2">${charts}</div>
    ${massBars(r.mass_kg, s.mtom_kg)}
    <div class="grid2">
      <div class="card"><h3>Point performance</h3>${kv(r.performance)}</div>
      <div class="card"><h3>Mission (${fmt(rec.mission.v_cruise_kmh, 0)} km/h at ${fmt(rec.mission.h_cruise_m, 0)} m, ${fmt(rec.mission.reserve_min, 0)} min reserve)</h3>${kv(r.mission)}</div>
    </div>
    <div class="grid2">
      <div class="card"><h3>Design drivers</h3><table><tbody>
        <tr><td>Wing loading set by</td><td>${esc(d.wing_loading_driver)}</td></tr>
        <tr><td>Installed power set by</td><td>${esc(d.power_driver)}</td></tr>
        <tr><td>P/W required by constraint [W/kg]</td><td>${pwreq}</td></tr>
        <tr><td>Wing-loading limits [kg/m²]</td><td>${wsl}</td></tr>
        <tr><td>V_S0 limit</td><td>${fmt(d.vs0_limit_kt, 0)} KCAS</td></tr>
        <tr><td>C_D0 source</td><td>${esc(d.cd0_source)}</td></tr>
        <tr><td>C_D0 parts</td><td>${cd0p}</td></tr>
        <tr><td>Chain efficiency (battery → thrust)</td><td>${fmt(d.eta_chain_cruise, 3)}</td></tr>
        <tr><td>Battery sized by</td><td>${esc(r.closure.battery_driver)}</td></tr>
        <tr><td>Weight method · calibration k_struct · k_sys</td><td>${esc(rec.assumptions.weight_method)} ·
          ${r.calibration.k_struct == null ? "not applied" : `${fmt(r.calibration.k_struct, 3)} · ${fmt(r.calibration.k_sys, 3)}`}</td></tr>
        <tr><td>C_D0 calibration k_cd0</td><td>${rec.assumptions.calibrate_cd0 ? fmt(r.calibration.k_cd0, 3) : "not applied"}</td></tr>
      </tbody></table></div>
      <div class="card"><h3>Geometry</h3><table><tbody>
        <tr><td>Wing S · b · AR · taper</td><td>${fmt(geo.S, 2)} m² · ${fmt(geo.b, 2)} m · ${fmt(geo.AR, 2)} · ${fmt(geo.taper, 2)}</td></tr>
        <tr><td>Root chord · MAC</td><td>${fmt(geo.c_root, 2)} m · ${fmt(geo.mac, 2)} m</td></tr>
        <tr><td>Fuselage L × W × H</td><td>${fmt(geo.fus_len, 2)} × ${fmt(geo.fus_width, 2)} × ${fmt(geo.fus_height, 2)} m (${esc(geo.fus_len_driver)} length)</td></tr>
        <tr><td>Seats abreast · rows</td><td>${geo.abreast} · ${geo.rows}</td></tr>
        <tr><td>Tail arm · S_HT · S_VT</td><td>${fmt(geo.tail_arm, 2)} m · ${fmt(geo.S_ht, 2)} m² · ${fmt(geo.S_vt, 2)} m²</td></tr>
        <tr><td>Wetted area wing · fuselage · tails</td><td>${fmt(geo.S_wet_wing, 1)} · ${fmt(geo.S_wet_fus, 1)} · ${fmt(geo.S_wet_ht + geo.S_wet_vt, 1)} m²</td></tr>
      </tbody></table></div>
    </div>
    ${regressionCard(r, s)}
    <div class="card"><h3>Inputs and assumptions changed from default</h3><div class="tbl-wrap">${usedHtml}</div></div>`;
  $("#loadcur").onclick = () => { setValues(rec); $("#label").value = rec.label; };
  bindHs1Buttons($("#result"));
  renderHistory();
  showStoredHs1(rec.id);
}

function regressionCard(r, s) {
  const f = r.calibration.regression;
  if (!f) return "";
  const lo = f.mtom_min_kg * 0.8, hi = Math.max(f.mtom_max_kg, s.mtom_kg) * 1.1;
  const xs = Array.from({ length: 30 }, (_, i) => lo + (hi - lo) * i / 29);
  const seats = f.c == null ? 1 : S.current.inputs.seats;
  const fitY = xs.map((x) => f.a * x ** f.b * (f.c == null ? 1 : seats ** f.c));
  const chart = lineChart({
    title: "Electric-aircraft regression: airframe mass vs MTOM", xlabel: "MTOM [kg]", ylabel: "Airframe mass excl. battery and powertrain [kg]",
    series: [{ name: `fit: ${f.form}${f.c == null ? "" : ` (seats = ${seats})`}`, x: xs, y: fitY }],
    points: [...f.points.map((p) => ({ x: p.mtom_kg, y: p.airframe_kg, r: 4, color: p.battery_estimated ? "var(--s5)" : "var(--s3)" })),
      { x: s.mtom_kg, y: r.mass_kg.airframe_regression, label: "this design" }], ymin: 0,
    note: "Green: battery mass published. Amber: battery mass estimated from kWh. Aircraft are listed in the table.",
  });
  const rows = f.points.map((p) => `<tr><td>${esc(p.name)}</td><td class="n">${fmt(p.mtom_kg, 0)}</td><td class="n">${p.seats}</td>` +
    `<td class="n">${fmt(p.airframe_kg, 1)}${p.battery_estimated ? " *" : ""}</td><td class="n">${fmt(p.predicted_kg, 1)}</td>` +
    `<td class="n">${fmt(p.error_pct, 1)}%</td><td>${esc(p.confidence)}</td></tr>`).join("");
  return `<div class="grid2">${chart}<div class="card"><h3>Regression fit</h3>
    <p class="note">${esc(f.form)}: a = ${fmt(f.a, 4)}, b = ${fmt(f.b, 3)}${f.c == null ? "" : `, c = ${fmt(f.c, 3)}`} ·
    n = ${f.n} · R² = ${fmt(f.r2, 3)} · rms error ${fmt(f.rms_error_pct, 1)}% · data ${fmt(f.mtom_min_kg, 0)}–${fmt(f.mtom_max_kg, 0)} kg.
    Airframe = empty mass excl. battery, less the modelled e-powertrain and propeller.
    * battery mass estimated from kWh at ${fmt(S.current.assumptions.regression_pack_wh_kg, 0)} Wh/kg.
    Dataset and sources: eacsize/data/electric_aircraft.json.</p>
    <div class="tbl-wrap"><table><thead><tr><th>Aircraft</th><th>MTOM</th><th>Seats</th><th>Airframe kg</th><th>Fit kg</th><th>Error</th><th>Data</th></tr></thead>
    <tbody>${rows}</tbody></table></div></div></div>`;
}

function diffFromDefaults(rec) {
  const out = [];
  for (const sec of SECTIONS) for (const f of S.schema[sec]) {
    const v = rec[sec]?.[f.key];
    if (v !== undefined && String(v) !== String(f.default)) out.push({ label: f.label, value: v, def: f.default, a: f.assumption });
  }
  return out;
}

// ---------------------------------------------------------------------------------------
// history and comparison
// ---------------------------------------------------------------------------------------
function renderHistory() {
  const runs = [...S.runs].reverse();
  if (!runs.length) { $("#history").innerHTML = ""; return; }
  const rows = runs.map((r) => {
    const s = r.summary, cur = S.current && S.current.id === r.id;
    return `<tr class="clickable${cur ? " sel" : ""}" data-id="${r.id}">
      <td><input type="checkbox" data-sel="${r.id}" ${S.selected.has(r.id) ? "checked" : ""} aria-label="Select ${r.id} for comparison"></td>
      <td>${esc(r.id)}</td><td>${esc(r.label)}</td><td><span class="pill ${r.status}">${r.status}</span></td>
      <td>${esc(r.mode === "solve_mtom" ? "solve" : "fixed")}</td><td class="n">${r.seats}</td><td class="n">${fmt(r.pack_wh_kg, 0)}</td>
      <td class="n">${fmt(s.mtom_kg, 0)}</td><td class="n">${fmt(s.battery_kwh, 1)}</td><td class="n">${fmt(s.installed_power_kw, 0)}</td>
      <td class="n">${fmt(s.ld_max, 1)}</td><td class="n">${fmt(s.range_cruise_km, 0)}</td><td class="n">${fmt(s.endurance_vmp_h, 2)}</td>
      <td class="note">${esc(r.timestamp.replace("T", " "))}</td>
      <td><button class="small" type="button" data-hs1="${esc(r.id)}" aria-label="Run the HS1 weights pipeline for ${esc(r.id)}">${hs1Label(r.id)}</button></td>
      <td><button class="small" type="button" data-del="${r.id}" aria-label="Delete ${r.id}">Delete</button></td></tr>`;
  }).join("");
  $("#history").innerHTML = `<div class="card"><div class="histbar"><h3>Previous runs (${runs.length})</h3>
    <span class="hs1opts">${hs1Options()}<button type="button" id="cmp" ${S.selected.size < 2 ? "disabled" : ""}>Compare selected (${S.selected.size})</button></span></div>
    <div class="tbl-wrap"><table><thead><tr><th></th><th>Run</th><th>Label</th><th>Status</th><th>Mode</th><th>Seats</th><th>Wh/kg</th>
    <th>MTOM kg</th><th>kWh</th><th>kW</th><th>L/D</th><th>Range km</th><th>End. h</th><th>Time</th><th>HS1</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>
    ${H.status && !H.status.available ? `<p class="banner">HS1 weights buttons are disabled: ${esc(H.status.reason || "")}</p>` : ""}
    <p class="note">Click a row to show that run. Tick two or more to compare them. <b>HS1 weights</b> runs the
    WeightEstimation pipeline for that run and shows the result as a card above.</p></div>`;
  $("#history").querySelectorAll("tr.clickable").forEach((tr) => tr.addEventListener("click", async (e) => {
    if (e.target.closest("input,button")) return;
    renderResult(await api(`/api/runs/${tr.dataset.id}`));
    $("#results").scrollTo({ top: 0, behavior: "smooth" });
  }));
  $("#history").querySelectorAll("[data-sel]").forEach((cb) => cb.addEventListener("change", () => {
    cb.checked ? S.selected.add(cb.dataset.sel) : S.selected.delete(cb.dataset.sel);
    renderHistory();
  }));
  $("#history").querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", async () => {
    if (!confirm(`Delete run ${b.dataset.del} from runs.json and runs.csv?`)) return;
    await api(`/api/runs/${b.dataset.del}`, { method: "DELETE" });
    S.selected.delete(b.dataset.del);
    dropHs1(b.dataset.del);
    await refreshRuns();
    if (S.current && S.current.id === b.dataset.del) {
      S.current = null;
      if (S.runs.length) renderResult(await api(`/api/runs/${S.runs[S.runs.length - 1].id}`));
      else $("#result").innerHTML = '<p class="empty">No runs stored.</p>';
    }
  }));
  const cmp = $("#cmp");
  if (cmp) cmp.onclick = compareSelected;
  bindHs1Buttons($("#history"));
  for (const k of ["close", "mc"]) {
    const cb = $(`#hs1${k}`);
    if (cb) cb.onchange = () => { H.opts[k] = cb.checked; };
  }
}

async function compareSelected() {
  const ids = S.runs.map((r) => r.id).filter((id) => S.selected.has(id));
  const recs = await Promise.all(ids.map((id) => api(`/api/runs/${id}`)));
  const diffRows = [];
  for (const sec of SECTIONS) for (const f of S.schema[sec]) {
    const vals = recs.map((r) => r[sec]?.[f.key]);
    if (new Set(vals.map(String)).size > 1) diffRows.push({ label: f.label, a: f.assumption, unit: f.unit, vals });
  }
  const res = ["mtom_kg", "oew_excl_battery_kg", "payload_kg", "battery_kg", "battery_kwh", "battery_mass_frac", "wing_area_m2", "span_m",
    "wing_loading_kgm2", "installed_power_kw", "cd0", "ld_max", "range_cruise_km", "range_cruise_eol_km", "endurance_vmp_h", "vs0_kcas",
    "takeoff_15m_m", "roc_sl_cont_ms"];
  const head = `<tr><th>Parameter</th>${recs.map((r) => `<th>${esc(r.id)}<br><span class="note">${esc(r.label)}</span></th>`).join("")}<th></th></tr>`;
  const body = `<tr><td colspan="${recs.length + 2}"><b>Inputs that differ</b></td></tr>` +
    (diffRows.length ? diffRows.map((d) => `<tr><td>${d.a ? '<span class="tag">A</span> ' : ""}${esc(d.label)}</td>${d.vals.map((v) => `<td class="n">${fmt(v, 3)}</td>`).join("")}<td>${esc(d.unit || "")}</td></tr>`).join("")
      : `<tr><td colspan="${recs.length + 2}" class="note">none</td></tr>`) +
    `<tr><td colspan="${recs.length + 2}"><b>Results</b></td></tr>` +
    `<tr><td>Status</td>${recs.map((r) => `<td><span class="pill ${r.results.status}">${r.results.status}</span></td>`).join("")}<td></td></tr>` +
    res.map((k) => `<tr><td>${esc(lab(k))}</td>${recs.map((r) => `<td class="n">${fmt(r.results.summary[k], dec(k))}</td>`).join("")}<td>${esc(unit(k))}</td></tr>`).join("");
  $("#compare").innerHTML = `<div class="card"><div class="histbar"><h3>Comparison</h3><button class="small" type="button" id="cmpclose">Close</button></div>
    <div class="tbl-wrap"><table><thead>${head}</thead><tbody>${body}</tbody></table></div></div>`;
  $("#cmpclose").onclick = () => { $("#compare").innerHTML = ""; };
  $("#compare").scrollIntoView({ behavior: "smooth" });
}

async function refreshRuns() {
  S.runs = await api("/api/runs");
  renderHistory();
}

// ---------------------------------------------------------------------------------------
// HS1 weights pipeline (WeightEstimation / hs1_weights), on demand for each stored run
// ---------------------------------------------------------------------------------------
const H = { status: null, done: new Set(), busy: new Set(), shown: new Map(), order: [], opts: { close: true, mc: true } };
const SEV = { info: "pass", warn: "warn", error: "fail" };

async function loadHs1Status() {
  try {
    H.status = await api("/api/hs1"); H.done = new Set(H.status.results);
    if (!H.status.available) H.status.reason = `WeightEstimation or the overlay was not found (${H.status.hs1_dir}).`;
  } catch (e) {
    // a server started before the HS1 feature existed serves the new page but has no /api/hs1 route
    H.status = { available: false, reason: e.message === "not found"
      ? "This dashboard server was started before the HS1 weights feature was added. Restart it (Ctrl+C, then python3 -m eacsize) and reload the page."
      : `HS1 status unavailable: ${e.message}` };
  }
}

function hs1Label(id) {
  return H.busy.has(id) ? "Running HS1…" : H.done.has(id) ? "Re-run HS1" : "HS1 weights";
}

function hs1Options() {
  const dis = H.status && H.status.available ? "" : "disabled";
  return `<label class="inline" title="MTOW closure with the eacsize mission energy"><input type="checkbox" id="hs1close" ${H.opts.close ? "checked" : ""} ${dis}> HS1 closure</label>` +
    `<label class="inline" title="Per-sample Monte Carlo closure (about 5 s more)"><input type="checkbox" id="hs1mc" ${H.opts.mc ? "checked" : ""} ${dis}> Monte Carlo</label>`;
}

function bindHs1Buttons(root) {
  root.querySelectorAll("[data-hs1]").forEach((b) => {
    const id = b.dataset.hs1;
    const ok = H.status && H.status.available;
    b.disabled = !ok || H.busy.has(id);
    b.title = ok ? "Run the WeightEstimation pipeline for this run" : (H.status?.reason || "HS1 weights unavailable");
    b.onclick = (e) => { e.stopPropagation(); runHs1(id); };
  });
  root.querySelectorAll("[data-hs1close]").forEach((b) => {
    b.onclick = () => { H.order = H.order.filter((x) => x !== b.dataset.hs1close); renderHs1Cards(); };
  });
}

function refreshHs1Buttons() {
  renderHistory();
  const cur = $("#hs1cur");
  if (cur) { cur.textContent = hs1Label(cur.dataset.hs1); bindHs1Buttons($("#result")); }
}

function front(id) { H.order = [id, ...H.order.filter((x) => x !== id)]; }

async function runHs1(id) {
  if (H.busy.has(id)) return;
  H.busy.add(id); front(id); refreshHs1Buttons(); renderHs1Cards(id);
  try {
    const res = await api(`/api/runs/${id}/hs1`, { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ close: H.opts.close, mc: H.opts.close && H.opts.mc }) });
    H.shown.set(id, res); H.done.add(id);
  } catch (e) {
    H.shown.set(id, { run_id: id, error: e.message });
  } finally {
    H.busy.delete(id); refreshHs1Buttons(); renderHs1Cards(id);
  }
}

async function showStoredHs1(id) {
  if (!H.done.has(id) || H.busy.has(id)) return;
  if (!H.shown.has(id)) {
    try { H.shown.set(id, await api(`/api/runs/${id}/hs1`)); } catch { return; }
  }
  front(id); renderHs1Cards();
}

function dropHs1(id) {
  H.done.delete(id); H.shown.delete(id); H.order = H.order.filter((x) => x !== id); renderHs1Cards();
}

function renderHs1Cards(focus) {
  const box = $("#hs1cards");
  box.innerHTML = H.order.map((id) => H.busy.has(id) ? hs1Running(id) : hs1Card(id, H.shown.get(id))).join("");
  bindHs1Buttons(box);
  if (focus) document.getElementById(`hs1-${focus}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
}

function hs1Head(id, title, pill, meta) {
  return `<div class="rhead"><h2>HS1 weights · ${esc(id)}${title ? ` · ${esc(title)}` : ""}</h2>${pill}` +
    `<span class="meta">${meta || ""}</span>` +
    `<button class="small" type="button" data-hs1="${esc(id)}">${hs1Label(id)}</button>` +
    `<button class="small" type="button" data-hs1close="${esc(id)}" aria-label="Hide this card">Hide</button></div>`;
}

function hs1Running(id) {
  return `<div class="card hs1" id="hs1-${esc(id)}">${hs1Head(id, "", '<span class="pill warning">running</span>',
    "WeightEstimation pipeline: predict, then closure with the eacsize mission energy …")}</div>`;
}

const dist = (d, n = 0) => (d ? `${fmt(d.p50, n)}<small>P10–P90 ${fmt(d.p10, n)}–${fmt(d.p90, n)}</small>` : "–");
const kpiBox = (k, v, note) => `<div class="kpi"><div class="k">${esc(k)}</div><div class="v">${v}</div>${note ? `<div class="k">${note}</div>` : ""}</div>`;

function hs1Card(id, x) {
  if (!x) return "";
  if (x.error) {
    return `<div class="card hs1" id="hs1-${esc(id)}">${hs1Head(id, "", '<span class="pill fail">failed</span>', "")}` +
      `<div class="error">${esc(x.error)}</div></div>`;
  }
  const rep = x.report, cal = rep.calibration, cp = x.closure_point, mc = x.closure_mc;
  const errs = rep.rule_flags.filter((f) => f.severity === "error").length;
  const warns = rep.rule_flags.filter((f) => f.severity === "warn").length;
  const pill = `<span class="pill ${errs ? "fail" : warns ? "warn" : "pass"}">${errs ? errs + " error" : warns ? warns + " warn" : "ok"}</span>`;
  const meta = `${esc((x.generated || "").replace("T", " "))} · ${fmt(x.runtime_s, 1)} s · k: ${esc(cal.engine)} · ` +
    `${rep.versions.n_samples} samples · seed ${rep.versions.seed}`;
  const banner = cal.engine === "prior"
    ? `<p class="banner">No fleet calibration yet: technology factors k are drawn from their priors. HS1 detail that eacsize
       does not model (DEP split, bays, equipment, materials, stations, bottom-up priors) comes from
       <code>${esc(x.overlay)}</code>, which holds illustrative placeholders. Treat these numbers as indicative.</p>` : "";
  let kpis = kpiBox("Empty weight W_E [kg]", dist(rep.empty_weight)) +
    kpiBox("W_E + payload at eacsize MTOM [kg]", dist(rep.mtow), `eacsize MTOM ${fmt(x.eacsize_mtom_kg, 0)} kg`);
  if (cp) {
    kpis += kpiBox("Closed MTOW [kg]", fmt(cp.mtow_kg, 0), `${cp.converged ? "converged" : "NOT converged"} in ${cp.iterations} it. · eacsize ${fmt(x.eacsize_mtom_kg, 0)} kg`) +
      kpiBox("Pack energy after closure [kWh]", fmt(cp.energy_wh / 1000, 1), `eacsize ${fmt(x.eacsize_battery_kwh, 1)} kWh`);
  }
  if (mc) {
    kpis += kpiBox("Monte Carlo closed MTOW [kg]", dist(mc.mtow)) +
      kpiBox("Samples that closed", `${fmt(mc.converged_fraction * 100, 1)}<small>%</small>`,
        mc.diverged_fraction ? `${fmt(mc.diverged_fraction * 100, 1)}% weight snowball` : "");
  }
  const comp = x.comparison.map((r) => `<tr><td>${esc(r.component.replace(/_/g, " "))}</td><td class="n">${fmt(r.eacsize_kg, 1)}</td>` +
    `<td class="n">${fmt(r.p10, 1)}</td><td class="n">${fmt(r.p50, 1)}</td><td class="n">${fmt(r.p90, 1)}</td>` +
    `<td class="n">${r.p50 - r.eacsize_kg >= 0 ? "+" : ""}${fmt(r.p50 - r.eacsize_kg, 1)}</td></tr>`).join("");
  const flags = rep.rule_flags.map((f) => `<tr><td>${esc(f.id)}</td><td><span class="pill ${SEV[f.severity] || "warn"}">${esc(f.severity)}</span></td><td>${esc(f.message)}</td></tr>`).join("");
  const sob = Object.entries(rep.sobol || {});
  const smax = Math.max(0.01, ...sob.map(([, v]) => v));
  const sobol = sob.map(([k, v]) => `<div class="row"><span title="${esc(k)}">${esc(k.replace(/^powertrain\./, "").replace(/^battery_bays\./, "bays."))}</span>` +
    `<div class="bar" style="width:${(Math.max(v, 0) / smax * 100).toFixed(1)}%;background:var(--s4)"></div><span class="n">${fmt(v, 2)}</span></div>`).join("");
  const comps = Object.entries(rep.components).map(([k, c]) => `<tr><td>${esc(k.replace(/_/g, " "))}${c.transcription_verified ? "" : ' <span class="tag" title="equation transcription not verified">U</span>'}</td>` +
    `<td class="n">${fmt(c.mass_kg.p10, 1)}</td><td class="n">${fmt(c.mass_kg.p50, 1)}</td><td class="n">${fmt(c.mass_kg.p90, 1)}</td>` +
    `<td>${esc(c.method)}</td><td class="n">${c.k ? fmt(c.k.p50, 3) : "–"}</td><td class="note">${esc(c.governing_load_case)}</td></tr>`).join("");
  const cg = rep.cg ? Object.entries(rep.cg.cases).map(([k, c]) => `<tr><td>${esc(k.replace(/_/g, " "))}</td><td class="n">${fmt(c.mass_kg.p50, 0)}</td>` +
    `<td class="n">${fmt(c.x_m.p50, 2)}</td><td class="n">${c.pct_mac ? fmt(c.pct_mac.p50, 1) : "–"}</td>` +
    `<td class="n">${c.tip_over_risk_frac == null ? "–" : fmt(c.tip_over_risk_frac * 100, 0) + "%"}</td></tr>`).join("") : "";
  const st = rep.stall_area_min_m2;
  return `<div class="card hs1" id="hs1-${esc(id)}">${hs1Head(id, x.run_label, pill, meta)}${banner}
    <div class="kpis">${kpis}</div>
    <div class="grid2" style="margin-top:14px">
      <div><h3>eacsize vs HS1 weights [kg]</h3><div class="tbl-wrap"><table><thead><tr><th>Component</th><th>eacsize</th><th>P10</th><th>P50</th><th>P90</th><th>Δ P50</th></tr></thead><tbody>${comp}</tbody></table></div></div>
      <div><h3>Rule checks</h3><div class="tbl-wrap"><table><tbody>${flags}</tbody></table></div>
        <p class="note">Min. wing area: V_S1 ≤ 59 kt ${fmt(st.VS1_59kt_clean.p50, 2)} m², V_S0 ≤ 61 kt ${fmt(st.VS0_61kt_landing.p50, 2)} m² (current ${fmt(st.current_area_m2, 2)} m², power-idle C_L,max).</p></div>
    </div>
    <div class="grid2" style="margin-top:14px">
      <div class="bars"><h3>Variance drivers of W_E (Sobol total index)</h3>${sobol || '<p class="note">not computed</p>'}</div>
      <div><h3>CG loading cases (P50)</h3>${cg ? `<div class="tbl-wrap"><table><thead><tr><th>Case</th><th>kg</th><th>x m</th><th>%MAC</th><th>tip-over</th></tr></thead><tbody>${cg}</tbody></table></div>` : '<p class="note">no stations in the overlay</p>'}</div>
    </div>
    <details class="more"><summary>All components (${Object.keys(rep.components).length})</summary><div class="tbl-wrap"><table><thead><tr><th>Component</th><th>P10</th><th>P50</th><th>P90</th><th>Method</th><th>k P50</th><th>Governing load case</th></tr></thead><tbody>${comps}</tbody></table></div>
      <p class="note"><span class="tag">U</span> equation transcription not yet verified against the source text.</p></details>
    <details class="more"><summary>Assumptions (${rep.assumptions.length}) · unverified criteria (${rep.unverified_criteria.length})</summary>
      <ul class="note">${rep.assumptions.map((a) => `<li>${esc(a)}</li>`).join("")}</ul>
      <ul class="note">${rep.unverified_criteria.map((a) => `<li>${esc(a)}</li>`).join("")}</ul></details>
    <p class="note"><a href="/api/runs/${encodeURIComponent(id)}/hs1" target="_blank" rel="noopener">Full result JSON</a> ·
      stored in runs/hs1_weights/${esc(id)}.json</p>
  </div>`;
}

// ---------------------------------------------------------------------------------------
// run
// ---------------------------------------------------------------------------------------
async function runDesign() {
  const btn = $("#run");
  btn.disabled = true; btn.textContent = "Running…";
  $("#error").hidden = true;
  try {
    const body = { label: $("#label").value.trim(), ...getValues() };
    const rec = await api("/api/run", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    S.runs = await api("/api/runs");
    renderResult(rec);
    $("#results").scrollTo({ top: 0, behavior: "smooth" });
  } catch (e) {
    $("#error").textContent = `Run failed: ${e.message}`;
    $("#error").hidden = false;
  } finally {
    btn.disabled = false; btn.textContent = "Run design";
  }
}

async function init() {
  const j = await api("/api/schema");
  S.schema = j.schema; S.presets = j.presets;
  const sel = $("#preset");
  sel.innerHTML = `<option value="">Defaults</option>` +
    Object.entries(S.presets).map(([k, p]) => `<option value="${esc(k)}">${esc(p.title || k)}</option>`).join("");
  sel.onchange = () => applyPreset(sel.value);
  buildForm();
  applyPreset("");
  $("#run").onclick = runDesign;
  $("#reset").onclick = () => { sel.value = ""; applyPreset(""); };
  await loadHs1Status();
  await refreshRuns();
  if (S.runs.length) {
    const last = await api(`/api/runs/${S.runs[S.runs.length - 1].id}`);
    renderResult(last);
    setValues(last);
    $("#label").value = last.label;
  }
}
init().catch((e) => { $("#error").textContent = `Could not load: ${e.message}`; $("#error").hidden = false; });
