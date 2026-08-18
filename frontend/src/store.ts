// The Zustand store — the ONLY bridge between the framework-free viewer and
// React (frontend/CLAUDE.md architectural rule). React components read this
// store; the viewer writes into it via event handlers registered here. No
// Three.js / Rapier object ever crosses this boundary — only plain data.
//
// Performance contract: writes happen only on DISCRETE events (ready, object
// loaded, selection change, stats at ~4 Hz, error). Per-frame transforms never
// enter this store.

import { create } from "zustand";
import { SobaViewer } from "./viewer/SobaViewer";
import type { ViewerStats } from "./viewer/types";

export type ViewerPhase = "boot" | "ready" | "error";

interface SobaState {
  phase: ViewerPhase;
  error: string | null;
  objectCount: number;
  selectedId: string | null;
  stats: ViewerStats | null;
}

export const useSobaStore = create<SobaState>(() => ({
  phase: "boot",
  error: null,
  objectCount: 0,
  selectedId: null,
  stats: null,
}));

// Create the viewer, mount it on `canvas`, and pipe its events into the store.
// Returns a cleanup that disposes the viewer (for React effect teardown).
// The SobaViewer instance itself never leaves this module.
export function attachViewer(canvas: HTMLCanvasElement): () => void {
  const set = useSobaStore.setState;
  const viewer = new SobaViewer();
  viewer.on("ready", ({ objects }) => set({ phase: "ready", objectCount: objects }));
  viewer.on("object-loaded", ({ count }) => set({ objectCount: count }));
  viewer.on("selection-changed", ({ id }) => set({ selectedId: id }));
  viewer.on("stats", (stats) => set({ stats }));
  viewer.on("error", (err) => set({ phase: "error", error: err.message }));
  viewer.mount(canvas).catch(() => { /* reported via the error event */ });
  return () => viewer.dispose();
}
