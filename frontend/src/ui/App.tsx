// App — DOM side. Owns the canvas element and the overlay panels; hands the
// canvas to the viewer through the store bridge and never touches Three.js or
// Rapier itself.

import { useEffect, useRef } from "react";
import { attachViewer } from "../store";
import { Hud } from "./panels/Hud";
import { EvalPanel } from "./panels/EvalPanel";

export default function App() {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => attachViewer(canvasRef.current!), []);

  return (
    <>
      <canvas id="c" ref={canvasRef} />
      <Hud />
      <EvalPanel />
    </>
  );
}
