// App — DOM side. Owns the canvas element and the edge-docked panels; hands
// the canvas to the viewer through the store bridge and never touches
// Three.js or Rapier itself. UI-level keyboard shortcuts (Escape, ?) live
// here; the physical ones (space, f) belong to the viewer.

import { useEffect, useRef, useState } from "react";
import {
  attachViewer, frameRoom, cycleColorMode, resetObjects, selectObject, setAutoOrbit,
  setPresentation, tourStep, useSobaStore,
} from "../store";
import { TooltipProvider } from "./components/tooltip";
import { EvalReportPanel } from "./panels/EvalReportPanel";
import { ScenePanel } from "./panels/ScenePanel";
import { ShortcutsOverlay } from "./panels/ShortcutsOverlay";
import { StatusBar } from "./panels/StatusBar";
import { StoryCard } from "./panels/StoryCard";
import { Toolbar } from "./panels/Toolbar";

// `staged`: set by the demo flow; shown on the story card so the "recorded
// run" label stays on screen after the reveal.
export default function App({ staged }: { staged?: string }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  const presentation = useSobaStore((s) => s.presentation);
  const orbiting = useRef(false);

  useEffect(() => attachViewer(canvasRef.current!), []);

  // one root attribute scales every rem-based token (index.css)
  useEffect(() => {
    const root = document.documentElement;
    if (presentation) root.dataset.presentation = "on"; else delete root.dataset.presentation;
    return () => { delete root.dataset.presentation; };
  }, [presentation]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) return;
      if (e.key === "?") {
        e.preventDefault();
        setShortcutsOpen((v) => !v);
      } else if (e.key === "p") {
        setPresentation(!useSobaStore.getState().presentation);
      } else if (e.key === "c") {
        cycleColorMode();
      } else if (e.key === "ArrowRight" || e.key === "n") {
        e.preventDefault();
        tourStep(1);
      } else if (e.key === "ArrowLeft") {
        e.preventDefault();
        tourStep(-1);
      } else if (e.key === "r") {
        resetObjects();
      } else if (e.key === "o") {
        // the viewer stops the orbit itself on any camera input, so this is
        // a request, not mirrored state: pressing o again re-arms or stops it
        orbiting.current = !orbiting.current;
        setAutoOrbit(orbiting.current);
      } else if (e.key === "f" && useSobaStore.getState().presentation) {
        frameRoom(); // on top of the viewer's own f: also clears the tour selection
      } else if (e.key === "Escape") {
        setShortcutsOpen((open) => {
          // Escape closes the overlay first; when it is already closed it
          // clears the selection instead.
          if (!open && useSobaStore.getState().selectedId !== null) selectObject(null);
          return false;
        });
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <TooltipProvider delayDuration={150}>
      <canvas id="c" ref={canvasRef} />
      {presentation ? (
        <StoryCard staged={staged} />
      ) : (
        <>
          <ScenePanel />
          <EvalReportPanel />
          <Toolbar
            shortcutsOpen={shortcutsOpen}
            onToggleShortcuts={() => setShortcutsOpen((v) => !v)}
          />
          <StatusBar />
        </>
      )}
      <ShortcutsOverlay open={shortcutsOpen} />
    </TooltipProvider>
  );
}
