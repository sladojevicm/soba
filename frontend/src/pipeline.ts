// run_metrics.json (src/telemetry/run_metrics.schema.json) reduced to what the
// UI shows. scene.json's frozen `geometry_source` only knows tsdf | generative,
// so "kept" and "completed" objects look the same there; the gate's real
// decision lives in the run's telemetry. Plain data, framework-free. Absent or
// malformed telemetry reduces to null and the UI falls back to scene.json.

import { sceneUrl } from "./viewer/loaders";

export type Route = "kept" | "completed" | "generated";

export interface ObjectPipeline {
  route: Route;
  /** completion method when one ran (patchcomplete | poisson_*), else null */
  completionMethod: string | null;
  coverageDeg: number | null;
  completeness: number | null;
  colliderParts: number | null;
}

export interface RunSummary {
  tier: number | null;
  startedAt: string | null;
  totalSeconds: number | null;
  stageSeconds: Record<string, number>;
  stageCounts: Record<string, number>;
  gateCounts: { tsdf: number; completion: number; generative: number };
  /** objects the gate saw (before drops and the 12-object cap) */
  gated: number;
  drops: Record<string, number>;
  completionCounts: Record<string, number>;
  stepCounts: Record<string, Record<string, number>>;
  /** ids the run assembled into its scene (may exceed a curated scene.json) */
  assembledIds: string[];
}

export interface PipelineData {
  byId: Record<string, ObjectPipeline>;
  run: RunSummary;
}

type Rec = Record<string, unknown>;
const isRec = (v: unknown): v is Rec => typeof v === "object" && v !== null && !Array.isArray(v);
const recs = (v: unknown): Rec[] => (Array.isArray(v) ? v.filter(isRec) : []);
const num = (v: unknown): number | null => (typeof v === "number" && isFinite(v) ? v : null);
const numMap = (v: unknown): Record<string, number> =>
  Object.fromEntries(Object.entries(isRec(v) ? v : {}).filter(([, n]) => typeof n === "number")) as Record<string, number>;

const ROUTES: Record<string, Route> = { tsdf: "kept", completion: "completed", generative: "generated" };

export function reducePipeline(raw: unknown): PipelineData | null {
  if (!isRec(raw) || raw.available === false || !isRec(raw.gate)) return null;
  const gate = raw.gate;
  const steps = isRec(raw.steps) ? raw.steps : {};
  const completion = isRec(raw.completion) ? raw.completion : {};

  // gate records carry track_id only; the per-object step/completion records
  // carry both track_id and the scene id, so they are the join.
  const idByTrack = new Map<unknown, string>();
  const partsById: Record<string, number> = {};
  for (const step of Object.values(steps)) {
    for (const r of recs(isRec(step) ? step.per_object : null)) {
      if (typeof r.id !== "string") continue;
      idByTrack.set(r.track_id, r.id);
      const parts = num(r.parts);
      if (parts !== null) partsById[r.id] = parts;
    }
  }
  const methodById: Record<string, string> = {};
  for (const r of recs(completion.per_object)) {
    if (typeof r.id !== "string") continue;
    idByTrack.set(r.track_id, r.id);
    if (typeof r.method === "string") methodById[r.id] = r.method;
  }

  const byId: Record<string, ObjectPipeline> = {};
  const gated = recs(gate.per_object);
  for (const g of gated) {
    const id = idByTrack.get(g.track_id);
    const route = ROUTES[String(g.routed ?? g.strategy)];
    if (!id || !route) continue;
    byId[id] = {
      route,
      completionMethod: methodById[id] ?? null,
      coverageDeg: num(g.angular_coverage_deg),
      completeness: num(g.completeness_ratio),
      colliderParts: partsById[id] ?? null,
    };
  }

  const stages = isRec(raw.stages) ? raw.stages : {};
  const stageSeconds: Record<string, number> = {};
  const stageCounts: Record<string, number> = {};
  for (const [k, v] of Object.entries(stages)) {
    if (!isRec(v)) continue;
    const s = num(v.seconds); if (s !== null) stageSeconds[k] = s;
    const c = num(v.count); if (c !== null) stageCounts[k] = c;
  }
  const counts = numMap(gate.counts);
  const run = isRec(raw.run) ? raw.run : {};
  return {
    byId,
    run: {
      tier: num(run.tier),
      startedAt: typeof raw.started_at === "string" ? raw.started_at : null,
      totalSeconds: stageSeconds.run ?? null,
      stageSeconds,
      stageCounts,
      gateCounts: { tsdf: counts.tsdf ?? 0, completion: counts.completion ?? 0, generative: counts.generative ?? 0 },
      gated: gated.length,
      drops: numMap(raw.drops),
      completionCounts: numMap(completion.counts),
      stepCounts: Object.fromEntries(
        Object.entries(steps).map(([k, v]) => [k, numMap(isRec(v) ? v.counts : null)])),
      assembledIds: Object.keys(partsById),
    },
  };
}

export async function fetchPipeline(): Promise<PipelineData | null> {
  try {
    const r = await fetch(sceneUrl("/run_metrics.json"), { cache: "no-store" });
    if (!r.ok) return null;
    return reducePipeline(await r.json());
  } catch {
    return null;
  }
}

// ---- wording (one place, so panel, story card and legend agree) -----------

const METHOD_NAMES: Record<string, string> = {
  patchcomplete: "PatchComplete",
  poisson_fallback: "Poisson (fallback)",
  poisson_local: "Poisson",
};

/** Truthful route label. Without telemetry, say only what scene.json knows. */
export function routeLabel(p: ObjectPipeline | undefined, geometrySource: "tsdf" | "generative"): string {
  if (!p) return geometrySource === "tsdf" ? "measured · tsdf" : "generated · image-to-3d";
  if (p.route === "kept") return "kept · tsdf";
  if (p.route === "generated") return "generated · image-to-3d";
  const m = p.completionMethod ? METHOD_NAMES[p.completionMethod] ?? p.completionMethod : "completion";
  return `completed · ${m}`;
}

// Route tints for the 3D view. The viewer cannot read CSS, so these mirror
// the --color-route-* tokens in index.css (same arrangement as the selection
// accent 0xffb454). Keep the two in step.
export const ROUTE_TINTS: Record<Route, number> = {
  kept: 0x8fa8ff,
  completed: 0x56c2e6,
  generated: 0xd48be8,
};

export const ROUTE_NAMES: Record<Route, string> = {
  kept: "kept · measured geometry good enough as is",
  completed: "completed · measured, then shape-completed",
  generated: "generated · regenerated from an image crop",
};

// Material tints mirror the --color-mat-* tokens the same way. The keys are
// the VLM's closed material list (src/scene/vlm_claude.py MATERIALS).
export const MATERIAL_TINTS: Record<string, number> = {
  wood: 0xb08d6a,
  metal: 0x9aa7b5,
  plastic: 0xe3ded2,
  rubber: 0x565c66,
  glass: 0xa8d8e0,
  ceramic: 0xefe9df,
  fabric: 0x7f93b8,
  paper: 0xf1ead2,
  stone: 0x8c8f8a,
  unknown: 0x8a93a2,
};
