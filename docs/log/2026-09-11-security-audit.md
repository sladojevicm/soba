# 2026-09-11 — Security phase A: secrets audit, dependency audit, pre-commit

Branch `feat/security-hardening` (PR into `develop`). Nothing runnable
changed; `STATUS.md` untouched.

## What was done

- `docs/security/secrets-audit.md`: inventory of every environment variable
  read in `src/`, `scripts/`, `deploy/`, `frontend/src/` (file:line, purpose,
  secret or not, default, how read); how the two secrets flow; ten gaps with
  phase-B recommendations; a threat-model table for the upload API.
- Root `env.example` with blank secrets and the commonly needed `SOBA_*`
  knobs; `deploy/runpod/env.example` kept as the pod template and
  cross-referenced from both sides. The undotted name is deliberate: the
  project's `.claude/settings.json` denies tool access to `.env.*`, which
  covers `.env.example`; renaming is a maintainer call.
- `docs/security/dependency-audit-2026-09-11.md`: raw `pip-audit` (venv and
  pyproject export) and `npm audit` (prod-only and all) output with triage.
- `.pre-commit-config.yaml`: `gitleaks` v8.30.1 + `ruff` v0.16.3 (no `--fix`,
  no `ruff-format` — 64 files are not format-clean).

## Findings

- Secrets: exactly two, `RUNPOD_API_KEY` (bearer header to
  `https://api.runpod.ai`, `generative.py:1207` / `_runsync`) and
  `ANTHROPIC_API_KEY` (presence-gated, read implicitly by the SDK,
  `vlm_claude.py:196`). None hardcoded; none reach the browser; no `.env*`
  ever tracked.
- gitleaks over 122 commits and the working tree: no leaks found.
- pip-audit (venv, and `-r` over the pyproject export): 0 vulnerabilities.
  The `recon` extra and pod-side model stacks are not installable here and
  remain unaudited (pod command in the audit doc).
- npm audit, prod-only and all 196 packages: 0 vulnerabilities.
- Worth fixing in phase B: no API auth; `SOBA_RUNPOD_URL` lets the bearer
  token go to any host; `SOBA_COMPC_CMD` is an executed command template; no
  body-size limit; `_ID_RE` / `_HULL_RE` are the only path guard; no CORS or
  security headers.

## Caveats

- `ruff check` has 127 findings on `develop`; the pre-commit `ruff` hook only
  runs on staged files, but `pre-commit run --all-files` is red until a lint
  clean-up PR.
- The venv is uv-managed with no `pip`; install tooling with
  `uv pip install --python .venv/bin/python <pkg>`.
