// The Zustand store — the ONLY bridge between the framework-free viewer and
// React (frontend/CLAUDE.md architectural rule). React components read this
// store; the viewer writes into it via event handlers registered here. UI ->
// viewer commands go through the exported actions. No Three.js / Rapier
// object ever crosses this boundary — only plain data.
//
// Performance contract: writes happen only on DISCRETE events (ready, object
// loaded, selection change, stats at ~4 Hz, error). Per-frame transforms never
// enter this store.

import { create } from "zustand";
import { fetchPipeline, MATERIAL_TINTS, ROUTE_TINTS, type PipelineData } from "./pipeline";
import { SobaViewer } from "./viewer/SobaViewer";
import type { ObjectInfo, ViewerStats } from "./viewer/types";

export type ColorMode = "off" | "material" | "route";
export type ViewerPhase = "boot" | "ready" | "error";

interface SobaState {
  phase: ViewerPhase;
  error: string | null;
  objects: ObjectInfo[];
  selectedId: string | null;
  stats: ViewerStats | null;
  /** the run's telemetry (gate route per object, stage timings); null when
   *  the scene folder has no run_metrics.json */
  pipeline: PipelineData | null;
  /** presentation mode: panels give way to the large-type story card */
  presentation: boolean;
  /** tint objects by material class or by the gate's route (rendering only) */
  colorMode: ColorMode;
}

const initialState: SobaState = {
  phase: "boot",
  error: null,
  objects: [],
  selectedId: null,
  stats: null,
  pipeline: null,
  presentation: false,
  colorMode: "off",
};

export const useSobaStore = create<SobaState>(() => ({ ...initialState }));

// Dev-only render-cause probe for the "orbiting causes ZERO React renders"
// rule: every store write comes from exactly one viewer event, so counting
// events by type shows what could have re-rendered. During an orbit, only
// `stats` (the explicitly allowed 4 Hz tick) may increment.
type EventCounts = Record<string, number>;
const devCounts: EventCounts | null = import.meta.env.DEV
  ? ((window as unknown as { __eventCounts: EventCounts }).__eventCounts =
      { ready: 0, "object-loaded": 0, "selection-changed": 0, stats: 0, error: 0 })
  : null;

let viewerRef: SobaViewer | null = null;

// Create the viewer, mount it on `canvas`, and pipe its events into the store.
// Returns a cleanup that disposes the viewer (for React effect teardown).
// The SobaViewer instance itself never leaves this module.
export function attachViewer(canvas: HTMLCanvasElement): () => void {
  const set = useSobaStore.setState;
  const viewer = new SobaViewer();
  viewerRef = viewer;
  viewer.on("ready", () => {
    set({ phase: "ready" });
    // one fetch per mount; a plain-data read that never touches the viewer
    void fetchPipeline().then((pipeline) => {
      if (viewerRef !== viewer) return;
      set({ pipeline });
      applyTints();
    });
  });
  viewer.on("object-loaded", ({ info }) => {
    set((s) => ({ objects: [...s.objects, info] }));
    applyTints(); // an object streamed in after the toggle gets its colour too
  });
  viewer.on("selection-changed", ({ id }) => {
    set({ selectedId: id });
    if (useSobaStore.getState().colorMode !== "off") applyTints(); // deselected object regains its tint
  });
  viewer.on("stats", (stats) => set({ stats }));
  viewer.on("error", (err) => set({ phase: "error", error: err.message }));
  if (devCounts) {
    for (const k of Object.keys(devCounts) as (keyof typeof devCounts)[]) {
      viewer.on(k as "ready", () => { devCounts[k]++; });
    }
  }
  viewer.mount(canvas).catch(() => { /* reported via the error event */ });
  return () => {
    viewer.dispose();
    if (viewerRef === viewer) viewerRef = null;
    // viewer-derived state resets; the two UI preferences survive a remount
    const { presentation, colorMode } = useSobaStore.getState();
    set({ ...initialState, presentation, colorMode });
  };
}

// ---- UI -> viewer actions (never expose the viewer itself) ----------------

/** Select from the UI: highlight in 3D without waking physics. */
export function selectObject(id: string | null): void {
  viewerRef?.selectById(id);
}

export function frameAll(): void {
  viewerRef?.frameAll();
}

export function dropBall(): void {
  viewerRef?.spawnBall();
}

// ---- presentation (frontend/CLAUDE.md: still only plain data crosses) -----

function applyTints(): void {
  const { colorMode, pipeline, objects } = useSobaStore.getState();
  if (colorMode === "off") { viewerRef?.setTints(null); return; }
  const tints: Record<string, number> = {};
  for (const o of objects) {
    if (colorMode === "material") {
      tints[o.id] = MATERIAL_TINTS[o.material] ?? MATERIAL_TINTS.unknown;
    } else {
      // without telemetry scene.json still separates measured from generated
      const route = pipeline?.byId[o.id]?.route ?? (o.geometrySource === "generative" ? "generated" : "completed");
      tints[o.id] = ROUTE_TINTS[route];
    }
  }
  viewerRef?.setTints(tints);
}

export function setPresentation(on: boolean): void {
  useSobaStore.setState({ presentation: on });
}

const COLOR_CYCLE: ColorMode[] = ["off", "material", "route"];
export function cycleColorMode(): void {
  const { colorMode } = useSobaStore.getState();
  useSobaStore.setState({ colorMode: COLOR_CYCLE[(COLOR_CYCLE.indexOf(colorMode) + 1) % COLOR_CYCLE.length] });
  applyTints();
}

export function frameAllNow(): void {
  viewerRef?.frameAll();
}

export function setAutoOrbit(on: boolean): void {
  viewerRef?.setAutoOrbit(on);
}

/** Tour: select + frame the next / previous object; past either end returns
 *  to the whole room with nothing selected. */
export function tourStep(delta: 1 | -1): void {
  const { objects, selectedId } = useSobaStore.getState();
  if (!objects.length) return;
  const i = objects.findIndex((o) => o.id === selectedId);
  const next = i === -1 ? (delta === 1 ? 0 : objects.length - 1) : i + delta;
  const id = next >= 0 && next < objects.length ? objects[next].id : null;
  viewerRef?.selectById(id);
  viewerRef?.focusObject(id);
}

export function frameRoom(): void {
  viewerRef?.selectById(null);
  viewerRef?.focusObject(null);
}

export function resetObjects(): void {
  viewerRef?.resetObjects();
}
