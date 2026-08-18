// App — DOM side. Owns the canvas element and the edge-docked panels; hands
// the canvas to the viewer through the store bridge and never touches
// Three.js or Rapier itself. UI-level keyboard shortcuts (Escape, ?) live
// here; the physical ones (space, f) belong to the viewer.

import { useEffect, useRef, useState } from "react";
import { attachViewer, selectObject, useSobaStore } from "../store";
import { TooltipProvider } from "./components/tooltip";
import { EvalReportPanel } from "./panels/EvalReportPanel";
import { ScenePanel } from "./panels/ScenePanel";
import { ShortcutsOverlay } from "./panels/ShortcutsOverlay";
import { StatusBar } from "./panels/StatusBar";
import { Toolbar } from "./panels/Toolbar";

export default function App() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [shortcutsOpen, setShortcutsOpen] = useState(false);

  useEffect(() => attachViewer(canvasRef.current!), []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) return;
      if (e.key === "?") {
        e.preventDefault();
        setShortcutsOpen((v) => !v);
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
      <ScenePanel />
      <EvalReportPanel />
      <Toolbar
        shortcutsOpen={shortcutsOpen}
        onToggleShortcuts={() => setShortcutsOpen((v) => !v)}
      />
      <ShortcutsOverlay open={shortcutsOpen} />
      <StatusBar />
    </TooltipProvider>
  );
}
