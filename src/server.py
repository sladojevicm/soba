"""Scene server entry point — a thin shim over `api.app.create_app()`.

The Phase-10 single-scene routes (`/`, `/scene.json`, `/meshes/{id}.glb`,
`/hulls/{stem}.glb`, `/events`, `/eval.json`) and their behaviour live in
`api/routes/scene.py`; the job API (`/api/jobs`, `/jobs/{id}/...`) in
`api/routes/jobs.py`; the composition in `api/app.py`. This module only keeps
the import paths that existed before the split:

  uvicorn server:app                      (module-level app, env/defaults)
  python scripts/serve.py --scene DIR     (main() below)
  from server import create_app           (tests)
"""

from __future__ import annotations

import os
from pathlib import Path

from api.app import create_app
from api.routes.scene import _HULL_RE, _ID_RE, GLB_MEDIA_TYPE

__all__ = ["GLB_MEDIA_TYPE", "_HULL_RE", "_ID_RE", "app", "create_app", "main"]

# Module-level app for `uvicorn server:app` (uses env/defaults).
app = create_app()


def main() -> None:
    import argparse

    import uvicorn

    ap = argparse.ArgumentParser(description="soba scene + job server")
    ap.add_argument("--scene", default=None, help="scene dir served at / (default out/scene_office_3)")
    ap.add_argument("--jobs-dir", default=None, help="job store + job dirs (default out/jobs)")
    ap.add_argument("--worker-mode", default=None, choices=["mock", "real", "gate-only"],
                    help="in-process worker mode (default mock; real/gate-only need the "
                         "recon extra and, for tiers >= 2, a GPU)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()

    if args.scene and not Path(args.scene).is_dir():
        # A scene dir WITHOUT scene.json is fine (objects may still be streaming
        # in); a path that is not a directory is a typo, and used to serve an
        # empty room without a word.
        ap.error(f"--scene {args.scene}: no such directory")
    if args.scene:
        os.environ["SOBA_SCENE_DIR"] = str(Path(args.scene).resolve())
    uvicorn.run(create_app(args.scene, jobs_dir=args.jobs_dir, worker_mode=args.worker_mode),
                host=args.host, port=args.port)


if __name__ == "__main__":
    main()
