# frontend — rules for the UI rebuild

The frontend is being rebuilt (Vite + React + TS + Tailwind v4 + shadcn/ui +
Zustand) around the existing Three.js/Rapier viewer. These rules are load-bearing;
violating any of them is a bug even if everything appears to work.

## Hard constraints — never violate

1. **Physics and rendering semantics are frozen.** Preserve exactly:
   - mass set on the rigid-body **descriptor** before `createRigidBody`
     (the P1/D5 fix — `setAdditionalMass` after creation is silently ignored)
   - CoACD hull colliders created with **density 0** (Rapier must never
     re-derive mass from hull geometry)
   - gravity and `ground.y` read **from `scene.json`**, never hardcoded
   - each `object_added` SSE event triggers a **re-GET of `/scene.json`** and a
     lookup by id (the event carries only the id)
   - `camera_pose` from `scene.json` wins initial camera placement;
     auto-framing follows streamed objects until the first user interaction;
     `f` re-frames at any time
2. **No React Three Fiber, no `@react-three/rapier`.** Three.js and Rapier stay
   imperative. React owns DOM only.
3. **`window.__soba` keeps working with the same shape:**
   `{objects: [{id, massKg, bodyType, position}], framedAll, screenPos(id)}`.
   `scripts/verify_browser.js` is the regression harness — it must pass
   (exit 0) at the end of every phase.
4. **No backend, no Node at runtime.** The built bundle is committed so
   `python scripts/serve.py` alone works for someone who has never run npm.
5. All existing controls stay: click = select, drag = push, space = drop ball,
   `f` = re-frame, orbit/zoom.

## The one architectural rule

> **React never touches a Three.js or Rapier object. The viewer never touches
> DOM outside its own canvas. Everything crosses through the Zustand store.**

`import three` inside a `.tsx` file, or `document.querySelector` inside
`viewer/`, means the design is broken — stop and restructure.

## Pinned versions

- `three@0.160.0`
- `@dimforge/rapier3d-compat@0.13.1`

These match what is vendored under `vendor/`. Do not upgrade. Keep `vendor/`
and its import map on disk until the Vite build is verified at parity
(Phase 2), then remove them.

## Design tokens — no arbitrary Tailwind values

Tailwind v4, CSS-first `@theme` config in one CSS file. **Do not create a
`tailwind.config.js`.** Arbitrary values (`text-[13px]`, `p-[7px]`,
`bg-[#1a1a1a]`) are forbidden — if a value isn't in the token set, add it to
the token set.

- Accent (selection only): `#ffb454` — matches the in-scene highlight.
- Eval quality only (never anything else): good `#7dd97b` / fair `#ffb454` /
  poor `#ff6b6b`.
- Surfaces: bg `#15171c`, text `#cdd3dc`, dim `#8a93a2`, hairline `#2a2e35`.
- 8px spacing scale, one elevation level, no gradients; motion 120–180ms
  ease-out on open/close and hover only; 12–13px body, 10–11px uppercase
  micro-labels, `tabular-nums` on changing numbers.

## Performance rules

- Per-frame transforms never enter React state. The rAF loop writes to Zustand
  outside React, or not at all.
- Only discrete events re-render React: selection change, object loaded,
  stats at ~4Hz.
- Orbiting the camera must cause **zero** React renders (verify with the React
  DevTools profiler).

## Verification

```bash
# fixture scene (no GPU scenes on this machine; real ones live on RunPod):
.venv/bin/python scripts/make_test_scene.py     # writes out/scene_test

SOBA_PYTHON=.venv/bin/python NODE_PATH=~/tmp/pptr/node_modules \
  node scripts/verify_browser.js --scene out/scene_test --screenshot /tmp/soba.png
```

Exit 0 required at the end of every phase. Look at the screenshot too —
passing checks with a black canvas is still a failure.

Note for the fixture: objects must NOT sit in exact flush contact with the
ground (y == half height, identity rotation) — that degenerate coplanar-face
case makes Rapier's narrow phase emit a bogus horizontal-normal manifold and
the position solver slides the body sideways to the ground corner.
`make_test_scene.py` floats everything 5 mm up.
