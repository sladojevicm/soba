import { createRoot } from "react-dom/client";
import App from "./ui/App";
import "./index.css";

const root = createRoot(document.getElementById("root")!);

// No <StrictMode>: its dev-mode double-mount would boot the viewer (Rapier
// world + SSE stream) twice. attachViewer's cleanup handles unmount correctly,
// but a deliberate single mount keeps dev behaviour identical to prod.
if (import.meta.env.DEV && window.location.pathname === "/kitchen-sink") {
  // Dev-only design-system reference (Phase 3). The DEV guard makes this
  // branch dead code in the committed bundle — no router needed.
  const { KitchenSink } = await import("./ui/dev/KitchenSink");
  root.render(<KitchenSink />);
} else {
  root.render(<App />);
}
