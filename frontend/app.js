// app.js — DOM-side bootstrap (Phase 1).
//
// The viewer core lives in SobaViewer.js and never touches DOM outside its
// canvas; this file owns the HUD and the eval panel and reacts to viewer
// events. This is the seam the Phase-2 React port will replace — React will
// own exactly what this file owns today, nothing more.

import { SobaViewer } from "./SobaViewer.js";

const hud = document.getElementById("hud");
const canvas = document.getElementById("c");
const log = (msg) => { hud.innerHTML = msg; };

function statusLine(count) {
  return `<b>Soba viewer</b>\n` +
    `objects: ${count}\n` +
    `<span class="key">click</span> select · ` +
    `<span class="key">drag</span> push · ` +
    `<span class="key">space</span> drop ball · ` +
    `<span class="key">f</span> frame all`;
}

// ---------------------------------------------------------------------------
// Ground-truth eval panel (ADDITIVE): shown only when the server has an
// eval.json for this scene (scripts/evaluate_scene.py). Scenes without one
// get {"available": false} (or, on an older server build, a 404) and render
// exactly as before — the panel is never injected and nothing throws.
// ---------------------------------------------------------------------------
const esc = (s) => String(s).replace(/[&<>"]/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

async function loadEvalPanel() {
  let ev = null;
  try {
    const r = await fetch("/eval.json", { cache: "no-store" });
    if (!r.ok) return;
    ev = await r.json();
  } catch { return; }
  // a real report always carries a score block; the "not available" marker
  // ({available:false}) and any malformed payload are silently ignored.
  if (!ev || !ev.score || typeof ev.score.value !== "number") return;

  const det = ev.detection || {};
  const geo = ev.geometry || {};
  const pose = ev.pose || {};
  const cls = (v) => (v >= 0.5 ? "good" : v >= 0.25 ? "fair" : "poor");
  const pct = (v) => (v == null ? "–" : Math.round(v * 100) + "%");

  const panel = document.createElement("details");
  panel.id = "evalPanel";
  const s = ev.score.value;
  panel.innerHTML = `
    <summary>ground truth: <span class="score ${cls(s / 100)}">${s.toFixed(0)}/100</span>
      <span class="dim">(${esc(ev.room ?? "")})</span></summary>
    <div class="body"></div>`;

  const rows = [];
  if (det.available) {
    rows.push(`objects: <b>${det.matched}</b> matched of <b>${det.gt_in_scope}</b> GT ` +
      `(shipped ${det.shipped})`);
    rows.push(`recall ${pct(det.recall)} · precision ${pct(det.precision)}` +
      (geo.mean_fscore_5cm != null ? ` · mean F@5cm ${pct(geo.mean_fscore_5cm)}` : ""));
    if (det.gt_out_of_scope) {
      rows.push(`<span class="dim">${det.gt_out_of_scope} GT objects outside ` +
        `pipeline scope (tv/plant/…)</span>`);
    }
  }
  rows.push(pose.available
    ? `ATE RMSE ${(pose.ate_rmse_m * 100).toFixed(2)} cm` +
      (pose.source === "dataset_ground_truth"
        ? ` <span class="dim">(pipeline used GT poses)</span>` : "")
    : `<span class="dim">pose eval n/a</span>`);

  let objTable = "";
  const objs = ev.objects || [];
  if (objs.length) {
    const tr = objs.map((o) => {
      const f = o.fscore_5cm;
      const fTxt = f == null ? "–" :
        `<span class="${cls(f)}">${f.toFixed(2)}</span>`;
      return `<tr><td>${esc(o.id)}</td><td>${fTxt}</td>` +
        `<td class="${o.matched ? cls(f ?? 0) : "poor"}">${esc(o.verdict ?? "")}</td></tr>`;
    }).join("");
    objTable = `<table><tr><th>object</th><th>F@5cm</th><th>verdict</th></tr>${tr}</table>`;
  }
  let missed = "";
  if ((ev.missed_gt || []).length) {
    missed = `<div class="dim" style="margin-top:4px">missed GT: ` +
      esc(ev.missed_gt.map((m) => m.class).join(", ")) + `</div>`;
  }

  panel.querySelector(".body").innerHTML =
    rows.map((r) => `<div>${r}</div>`).join("") + objTable + missed;
  document.body.appendChild(panel);
}

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------
const viewer = new SobaViewer();

viewer.on("ready", ({ objects }) => {
  log(statusLine(objects));
  // fire-and-forget: the eval panel never blocks or breaks the viewer
  loadEvalPanel().catch(() => {});
});
viewer.on("object-loaded", ({ count }) => log(statusLine(count)));
viewer.on("error", (err) => log("error: " + err.message));

log("initialising physics…");
viewer.mount(canvas).catch(() => { /* reported via the error event */ });
