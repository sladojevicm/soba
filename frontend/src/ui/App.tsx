// App — DOM side. Owns the canvas element and the edge-docked panels; hands
// the canvas to the viewer through the store bridge and never touches
// Three.js or Rapier itself.

import { useEffect, useRef } from "react";
import { attachViewer } from "../store";
import { TooltipProvider } from "./components/tooltip";
import { EvalReportPanel } from "./panels/EvalReportPanel";
import { ScenePanel } from "./panels/ScenePanel";
import { StatusBar } from "./panels/StatusBar";
import { Toolbar } from "./panels/Toolbar";

export default function App() {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => attachViewer(canvasRef.current!), []);

  return (
    <TooltipProvider delayDuration={150}>
      <canvas id="c" ref={canvasRef} />
      <ScenePanel />
      <EvalReportPanel />
      <Toolbar />
      <StatusBar />
    </TooltipProvider>
  );
}
