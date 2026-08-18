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
import { SobaViewer } from "./viewer/SobaViewer";
import type { ObjectInfo, ViewerStats } from "./viewer/types";

export type ViewerPhase = "boot" | "ready" | "error";

interface SobaState {
  phase: ViewerPhase;
  error: string | null;
  objects: ObjectInfo[];
  selectedId: string | null;
  stats: ViewerStats | null;
}

const initialState: SobaState = {
  phase: "boot",
  error: null,
  objects: [],
  selectedId: null,
  stats: null,
};

export const useSobaStore = create<SobaState>(() => ({ ...initialState }));

let viewerRef: SobaViewer | null = null;

// Create the viewer, mount it on `canvas`, and pipe its events into the store.
// Returns a cleanup that disposes the viewer (for React effect teardown).
// The SobaViewer instance itself never leaves this module.
export function attachViewer(canvas: HTMLCanvasElement): () => void {
  const set = useSobaStore.setState;
  const viewer = new SobaViewer();
  viewerRef = viewer;
  viewer.on("ready", () => set({ phase: "ready" }));
  viewer.on("object-loaded", ({ info }) =>
    set((s) => ({ objects: [...s.objects, info] })));
  viewer.on("selection-changed", ({ id }) => set({ selectedId: id }));
  viewer.on("stats", (stats) => set({ stats }));
  viewer.on("error", (err) => set({ phase: "error", error: err.message }));
  viewer.mount(canvas).catch(() => { /* reported via the error event */ });
  return () => {
    viewer.dispose();
    if (viewerRef === viewer) viewerRef = null;
    set({ ...initialState });
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
