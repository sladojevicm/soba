# Dependency audit — 2026-09-11

Run on the WSL development box (no GPU, `recon` extra not installed) from
`develop@9cbd012`. Result: **0 known vulnerabilities** in every audit. No
versions were changed in this PR; follow-ups are listed at the end.

## Python — installed venv (`pip-audit 2.10.1`)

`.venv` is uv-managed (`pip` absent); `pip-audit` was installed with
`uv pip install --python .venv/bin/python pip-audit`. This is the actionable
audit: it covers exactly what runs here (`dev` + `serve` extras, plus the
pip-audit tooling itself).

```
$ .venv/bin/pip-audit --progress-spinner off
No known vulnerabilities found
Name Skip Reason
---- -------------------------------------------------------------------
soba Dependency not found on PyPI and could not be audited: soba (0.0.1)
```

Packages audited (project runtime in bold, rest is dev/tooling):
**jsonschema 4.26.0, numpy 2.4.6, pyyaml 6.0.3, starlette 1.6.0, uvicorn
0.52.3, sse-starlette 3.4.8, trimesh 5.0.0, scipy 1.17.1, anyio 4.14.2, h11
0.16.0, httpcore 1.0.9, httpx 0.28.1**, pytest 9.1.1, ruff 0.16.3, plus
pip-audit's own tree (cachecontrol, cyclonedx-python-lib, requests 2.34.2,
rich, urllib3 2.7.0, …).

Not present in this venv and therefore not covered: `anthropic` (installed
ad hoc where the VLM step runs), the `recon` extra (`open3d`, `opencv-python`,
`pillow`, `scikit-image`, `pymeshfix`), torch and the model checkouts on the
pod.

## Python — pyproject export (`pip-audit -r`)

`pyproject.toml` pins only lower bounds, so `-r` resolves the *newest*
satisfying versions, not what the pod runs. Useful as a "would a fresh
install be clean" check, not as a statement about deployed versions.

```
$ .venv/bin/pip-audit --progress-spinner off -r req.txt
No known vulnerabilities found
```

`req.txt` = `dependencies` + `dev` + `serve` + `recon`:
`jsonschema>=4.21 numpy>=1.26 pyyaml>=6.0 pytest>=8.0 ruff>=0.5 httpx>=0.27
starlette>=0.37 uvicorn>=0.27 sse-starlette>=2.0 open3d>=0.18
opencv-python>=4.9 trimesh>=4.0 pillow>=10.0 scipy>=1.11 scikit-image>=0.22
pymeshfix>=0.16`. Resolved (via `uv pip compile`) to, among others:
open3d 0.19.0, opencv-python 5.0.0.93, pillow 12.3.0, scikit-image 0.26.0,
pymeshfix 0.18.1, trimesh 5.1.0, starlette 1.6.0, uvicorn 0.52.4,
sse-starlette 3.4.11, numpy 2.4.6, scipy 1.17.1.

## Frontend — `npm audit` (npm 11.12.1, Node 24.15.0)

Installed from `frontend/package-lock.json` with `npm ci`.

```
$ npm audit --omit=dev
found 0 vulnerabilities

$ npm audit
found 0 vulnerabilities
```

Metadata (both runs): `prod 46, dev 151, optional 76, total 196` packages;
`info 0, low 0, moderate 0, high 0, critical 0`.

Production dependencies (`npm ls --omit=dev --depth=0`):
`@dimforge/rapier3d-compat 0.13.1`, `@radix-ui/react-slider 1.4.7`,
`@radix-ui/react-tooltip 1.2.16`, `class-variance-authority 0.7.1`,
`clsx 2.1.1`, `lucide-react 0.539.0`, `react 18.3.1`, `react-dom 18.3.1`,
`tailwind-merge 3.6.0`, `three 0.160.0`, `zustand 5.0.15`.
`three` and `rapier3d-compat` are pinned by `frontend/CLAUDE.md` and must not
move without the maintainer.

## Triage

| Audit | Findings | Runtime / dev | Fix available | Action |
|---|---|---|---|---|
| pip-audit (venv) | 0 | — | — | none |
| pip-audit (pyproject export) | 0 | — | — | none |
| npm audit --omit=dev | 0 | — | — | none |
| npm audit (all) | 0 | — | — | none |

## Follow-ups (not done in this PR)

1. Run `pip-audit` on the **pod** environment (`recon` extra, torch, the
   MASt3R / TripoSG / Hunyuan3D / PatchComplete checkouts' requirements) —
   this box cannot install them. Command on the pod:
   `pip install pip-audit && pip-audit --progress-spinner off`.
2. Add a `requirements.lock` (or `uv lock`) so the venv audit is
   reproducible instead of depending on whatever was last installed.
3. Wire `pip-audit`, `npm audit --omit=dev` and `gitleaks` into CI once a
   workflow exists (`ci-agent` track).
4. Re-run before every release; record the date in this directory.
