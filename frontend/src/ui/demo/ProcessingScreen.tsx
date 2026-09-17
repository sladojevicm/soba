// Staged processing — a presenter-paced walk through the REAL pipeline stages
// with the numbers the recorded run wrote to run_metrics.json. Nothing here
// animates on its own: no ticking progress bar pretends a job is running. The
// presenter advances (→ / enter / click), and StagedBanner stays on screen.

import { useEffect } from "react";
import { ArrowRight } from "lucide-react";
import type { RunSummary } from "../../pipeline";
import { Button } from "../components/button";
import { Kbd } from "../components/kbd";
import { Panel, PanelHeader } from "../components/panel";
import { cn } from "../lib/utils";
import { formatDuration } from "./format";

interface StageRow { label: string; value: string; time?: string }
interface Act { context: string; title: string; blurb: string; rows: StageRow[]; note?: string }

const secs = (run: RunSummary | null, key: string) =>
  run?.stageSeconds[key] != null ? formatDuration(run.stageSeconds[key]) : undefined;
const n = (v: number | undefined | null) => (v == null ? "–" : String(v));
const dropText = (drops: Record<string, number>) =>
  Object.entries(drops).map(([k, v]) => `${v} ${k.replace(/_/g, " ")}`).join(", ") || "none";

function buildActs(run: RunSummary | null): Act[] {
  const g = run?.gateCounts;
  const fused = run?.stepCounts.fusion?.fused;
  const sealed = Object.values(run?.stepCounts.watertight_repair ?? {}).reduce((a, b) => a + b, 0);
  return [
    {
      context: "A",
      title: "Perception",
      blurb: "frames, depth, instance masks and camera poses become one typed bundle",
      rows: [
        { label: "open the bundle", value: "frames, depth, masks, poses", time: secs(run, "bundle_open") },
        { label: "objects tracked across frames", value: n(run?.gated) },
      ],
      note: "Benchmark captures (Replica) ship ground-truth masks and poses. Real captures use YOLO + SAM2 and MASt3R.",
    },
    {
      context: "B",
      title: "Reconstruction",
      blurb: "per-object geometry, and a confidence gate that decides how much to trust it",
      rows: [
        { label: "observed point cloud per object", value: n(run?.stageCounts.observed_cloud), time: secs(run, "observed_cloud") },
        {
          label: "confidence gate: keep / complete / regenerate",
          value: g ? `${g.tsdf} / ${g.completion} / ${g.generative}` : "–",
          time: secs(run, "gate"),
        },
        { label: "TSDF fusion", value: "measured geometry", time: secs(run, "tsdf_fuse") },
        { label: "shape completion (PatchComplete)", value: `${n(run?.completionCounts.patchcomplete)} objects, ${n(fused)} fused`, time: secs(run, "completion") },
        { label: "image-to-3D generation", value: `${n(run?.stageCounts.generate_object)} objects`, time: secs(run, "generation") },
        { label: "dropped", value: run ? dropText(run.drops) : "–" },
      ],
    },
    {
      context: "C",
      title: "Scene assembly",
      blurb: "physics from a vision-language model, mass from volume, convex colliders",
      rows: [
        { label: "VLM physics (material, friction, restitution)", value: "one batched call", time: secs(run, "vlm_infer") },
        { label: "watertight repair", value: `${sealed || "–"} meshes` },
        { label: "convex decomposition (CoACD)", value: `${n(run?.stageCounts.coacd)} objects`, time: secs(run, "coacd") },
        { label: "objects assembled into scene.json", value: n(run?.assembledIds.length) },
      ],
    },
  ];
}

export function ProcessingScreen({
  run, step, onStep, onDone,
}: { run: RunSummary | null; step: number; onStep: (s: number) => void; onDone: () => void }) {
  const acts = buildActs(run);
  const last = acts.length - 1;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "ArrowRight" || e.key === "Enter") {
        e.preventDefault();
        if (step >= last) onDone(); else onStep(step + 1);
      } else if (e.key === "ArrowLeft") {
        e.preventDefault();
        onStep(Math.max(0, step - 1));
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [step, last, onStep, onDone]);

  return (
    <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-6 px-6 py-4">
      <div className="grid w-full max-w-7xl grid-cols-1 gap-4 lg:grid-cols-3">
        {acts.map((act, i) => {
          const state = i < step ? "done" : i === step ? "active" : "todo";
          return (
            <Panel
              key={act.title}
              aria-current={state === "active" ? "step" : undefined}
              className={cn(
                "flex flex-col transition-opacity duration-150 ease-out",
                state === "todo" && "opacity-40",
                state === "active" && "border-accent"
              )}
            >
              <PanelHeader
                title={`Context ${act.context}`}
                actions={state === "done" ? <span className="text-micro uppercase tracking-label text-dim">done</span> : undefined}
              />
              <div className="flex flex-1 flex-col gap-3 p-4">
                <div>
                  <h2 className={cn("text-2xl font-medium", state === "todo" ? "text-dim" : "text-text-strong")}>{act.title}</h2>
                  <p className="mt-1 text-base text-dim">{act.blurb}</p>
                </div>
                {state !== "todo" && (
                  <div className="flex animate-panel-in flex-col border-t border-hairline/60 pt-2">
                    {act.rows.map((r) => (
                      <div key={r.label} className="flex items-baseline justify-between gap-3 py-1">
                        <span className="text-base text-dim">{r.label}</span>
                        <span className="shrink-0 text-right font-mono text-base tabular-nums text-text">
                          {r.value}
                          {r.time && <span className="ml-2 text-faint">{r.time}</span>}
                        </span>
                      </div>
                    ))}
                    {act.note && <p className="mt-2 text-sm text-faint">{act.note}</p>}
                  </div>
                )}
              </div>
            </Panel>
          );
        })}
      </div>

      <div className="flex items-center gap-3">
        <Button variant={step >= last ? "accent" : "default"} onClick={() => (step >= last ? onDone() : onStep(step + 1))}>
          {step >= last ? "Open the scene" : `Next: ${acts[step + 1].title}`} <ArrowRight className="size-3.5" />
        </Button>
        <span className="text-sm text-faint"><Kbd>→</Kbd> next · <Kbd>←</Kbd> back</span>
      </div>
      {!run && (
        <p className="text-sm text-faint">This scene folder has no run_metrics.json, so no recorded numbers are shown.</p>
      )}
    </div>
  );
}
