import { createRoot } from "react-dom/client";
import App from "./ui/App";
import "./index.css";

// No <StrictMode>: its dev-mode double-mount would boot the viewer (Rapier
// world + SSE stream) twice. attachViewer's cleanup handles unmount correctly,
// but a deliberate single mount keeps dev behaviour identical to prod.
createRoot(document.getElementById("root")!).render(<App />);
