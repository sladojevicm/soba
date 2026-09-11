import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Dev proxy: `npm run dev` gives HMR against the real Python scene server
// (scripts/serve.py on :8000). The /events route is SSE — proxied as a
// streaming response (http-proxy pipes chunked bodies; verified manually).
// /api (job upload + status) and /jobs (job-scoped scene routes) are proxied
// too, so http://localhost:5173/jobs/<id>/ works in dev; the viewer resolves
// its own fetches through sceneUrl() (viewer/loaders.ts).
const SCENE_SERVER = "http://127.0.0.1:8000";
const proxy = Object.fromEntries(
  ["/scene.json", "/meshes", "/hulls", "/events", "/eval.json", "/api", "/jobs"].map((p) => [
    p,
    { target: SCENE_SERVER, changeOrigin: true },
  ])
);

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  server: { proxy },
  build: {
    // the bundle is COMMITTED (frontend/CLAUDE.md constraint 4): the Python
    // server serves dist/ so `python scripts/serve.py` works with no Node.
    outDir: "dist",
    sourcemap: false,
    // three + rapier (wasm inlined as base64) are heavy; that is expected and
    // fine for a local tool — silence the size warning rather than split.
    chunkSizeWarningLimit: 4000,
  },
});
