_Written 2026-09-05 on branch `fix/phase3-pose-and-eval` (PR #5) and moved here on merge, 2026-09-10. Paths under `~/projects/vid2sim/` refer to the RTX 4060 machine._

Reviewer asked for an ablation isolating the confidence gate. All 21 cells built +
GT-evaluated (7 rooms × forced tsdf/completion/generative at tier-2 settings,
`scripts/ablation_routing.sh` → `out/abl_<room>_<strategy>/eval.json`,
aggregate with `scripts/ablation_summary.py`). Results + readings in
**BENCHMARK.md §4**; headline: gated routing best surface fidelity
(Chamfer 5.6 cm / F@5 0.68), forced tsdf/completion buy recall 0.83 by shipping
the 9-object poorly-observed tail at F@5 0.22–0.29, forced generative
reproduces tier 1 to rounding (kills the T1→T2 confound).
- **Paper (`~/Downloads/erkLaTeX/erk.tex`) updated for camera-ready**: Table 3
  extended with the three forced columns, ablation paragraph in §3.2,
  "outperforms every fixed strategy" claims reworded to the supported
  "yields more faithful surfaces than any fixed strategy" (intro (ii),
  Discussion, Conclusion). Squeezed back to exactly **4 pages** (footnotesize
  refs, "first author et al." bib entries, caption/prose trims, Table 4 [H]→[t],
  qual-figure 16 mm) — Termes-Bold verified. **Camera-ready deadline 7. 9.,
  upload via the ERK portal ("Uredi") — user action.**
- Note: the paper as of today names tiers 1–3 (tier 4 dropped from the paper),
  has a related-work paragraph (HoloScene/LiteReality/SimRecon/Video2Game),
  a measured per-tier runtime paragraph, and the qualitative figure with the
  LIVE browser screenshot (fig/q_*.png, rendered 2026-09-05).
