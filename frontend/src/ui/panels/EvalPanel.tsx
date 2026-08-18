// Ground-truth eval panel — Phase-2 parity port (same id, classes, and DOM
// shape as the pre-React version; styles in index.css are the originals).
//
// ADDITIVE: rendered only when the server has an eval.json for this scene
// (scripts/evaluate_scene.py). Scenes without one get {"available": false}
// (or, on an older server build, a 404) and render exactly as before — the
// panel returns null and nothing throws.

import { useEffect, useState } from "react";

interface EvalObjectRow {
  id: string;
  matched?: boolean;
  fscore_5cm?: number | null;
  verdict?: string;
}

interface EvalReport {
  score?: { value: number };
  room?: string;
  detection?: {
    available?: boolean;
    matched?: number;
    gt_in_scope?: number;
    shipped?: number;
    recall?: number | null;
    precision?: number | null;
    gt_out_of_scope?: number;
  };
  geometry?: { mean_fscore_5cm?: number | null };
  pose?: { available?: boolean; ate_rmse_m?: number; source?: string };
  objects?: EvalObjectRow[];
  missed_gt?: { class: string }[];
}

const cls = (v: number) => (v >= 0.5 ? "good" : v >= 0.25 ? "fair" : "poor");
const pct = (v: number | null | undefined) =>
  v == null ? "–" : Math.round(v * 100) + "%";

export function EvalPanel() {
  const [report, setReport] = useState<EvalReport | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await fetch("/eval.json", { cache: "no-store" });
        if (!r.ok) return;
        const ev = await r.json();
        // a real report always carries a score block; the "not available"
        // marker ({available:false}) and malformed payloads are ignored.
        if (!ev || !ev.score || typeof ev.score.value !== "number") return;
        if (!cancelled) setReport(ev);
      } catch { /* eval is optional — never break the viewer */ }
    })();
    return () => { cancelled = true; };
  }, []);

  if (!report) return null;

  const s = report.score!.value;
  const det = report.detection ?? {};
  const geo = report.geometry ?? {};
  const pose = report.pose ?? {};
  const objs = report.objects ?? [];
  const missed = report.missed_gt ?? [];

  return (
    <details id="evalPanel">
      <summary>
        {"ground truth: "}
        <span className={`score ${cls(s / 100)}`}>{s.toFixed(0)}/100</span>{" "}
        <span className="dim">({report.room ?? ""})</span>
      </summary>
      <div className="body">
        {det.available && (
          <>
            <div>
              {"objects: "}<b>{det.matched}</b>{" matched of "}
              <b>{det.gt_in_scope}</b>{` GT (shipped ${det.shipped})`}
            </div>
            <div>
              {`recall ${pct(det.recall)} · precision ${pct(det.precision)}`}
              {geo.mean_fscore_5cm != null && ` · mean F@5cm ${pct(geo.mean_fscore_5cm)}`}
            </div>
            {!!det.gt_out_of_scope && (
              <div>
                <span className="dim">
                  {det.gt_out_of_scope} GT objects outside pipeline scope (tv/plant/…)
                </span>
              </div>
            )}
          </>
        )}
        <div>
          {pose.available ? (
            <>
              {`ATE RMSE ${((pose.ate_rmse_m ?? 0) * 100).toFixed(2)} cm`}
              {pose.source === "dataset_ground_truth" && (
                <> <span className="dim">(pipeline used GT poses)</span></>
              )}
            </>
          ) : (
            <span className="dim">pose eval n/a</span>
          )}
        </div>
        {objs.length > 0 && (
          <table>
            <tbody>
              <tr><th>object</th><th>F@5cm</th><th>verdict</th></tr>
              {objs.map((o) => {
                const f = o.fscore_5cm;
                return (
                  <tr key={o.id}>
                    <td>{o.id}</td>
                    <td>{f == null ? "–" : <span className={cls(f)}>{f.toFixed(2)}</span>}</td>
                    <td className={o.matched ? cls(f ?? 0) : "poor"}>{o.verdict ?? ""}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
        {missed.length > 0 && (
          <div className="dim" style={{ marginTop: 4 }}>
            missed GT: {missed.map((m) => m.class).join(", ")}
          </div>
        )}
      </div>
    </details>
  );
}
