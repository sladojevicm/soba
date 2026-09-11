"""Soba service layer (bounded context E).

`api.app.create_app()` composes the legacy single-scene routes (`/`,
`/scene.json`, ...) with the job API (`/api/jobs`, `/jobs/{id}/...`).
`src/server.py` is a thin shim over it for `uvicorn server:app` and
`scripts/serve.py`.
"""
