// Ground-truth eval report (right dock) — rebuilt on the design system.
// Rendered only when the server has an eval.json for this scene; scenes
// without one ({"available": false} or 404) render nothing and never throw.
// Green/amber/red here is the ONE place semantic color is allowed.

import { useEffect, useState } from "react";
import { ChevronDown, ChevronUp } from "lucide-react";
import { selectObject, useSobaStore } from "../../store";
import { Button } from "../components/button";
import { Panel, PanelBody, PanelRow } from "../components/panel";
import { Table, TBody, Td, Th, THead, Tr } from "../components/table";
import { cn } from "../lib/utils";

interface EvalObjectRow {
  id: string;
  matched?: boolean;
  fscore_5cm?: number | null;
  verdict?: string;
}

interface EvalReport {
  score?: { value: number };
  room?: string;
  detection?: {
    available?: boolean;
    matched?: number;
    gt_in_scope?: number;
    shipped?: number;
    recall?: number | null;
    precision?: number | null;
    gt_out_of_scope?: number;
  };
  geometry?: { mean_fscore_5cm?: number | null };
  pose?: { available?: boolean; ate_rmse_m?: number; source?: string };
  objects?: EvalObjectRow[];
  missed_gt?: { class: string }[];
}

type Quality = "good" | "fair" | "poor";
const quality = (v: number): Quality => (v >= 0.5 ? "good" : v >= 0.25 ? "fair" : "poor");
const qualityText: Record<Quality, string> = {
  good: "text-good", fair: "text-fair", poor: "text-poor",
};
const pct = (v: number | null | undefined) =>
  v == null ? "–" : Math.round(v * 100) + "%";

export function EvalReportPanel() {
  const [report, setReport] = useState<EvalReport | null>(null);
  const [open, setOpen] = useState(true);
  const loadedIds = useSobaStore((s) => s.objects.map((o) => o.id).join(","));
  const selectedId = useSobaStore((s) => s.selectedId);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await fetch("/eval.json", { cache: "no-store" });
        if (!r.ok) return;
        const ev = await r.json();
        // a real report always carries a score block; the "not available"
        // marker ({available:false}) and malformed payloads are ignored.
        if (!ev || !ev.score || typeof ev.score.value !== "number") return;
        if (!cancelled) setReport(ev);
      } catch { /* eval is optional — never break the viewer */ }
    })();
    return () => { cancelled = true; };
  }, []);

  if (!report) return null;

  const s = report.score!.value;
  const det = report.detection ?? {};
  const geo = report.geometry ?? {};
  const pose = report.pose ?? {};
  const objs = report.objects ?? [];
  const missed = report.missed_gt ?? [];
  const loaded = new Set(loadedIds.split(","));

  return (
    <div className="pointer-events-none fixed top-2 right-2 bottom-10 z-10 flex w-80 animate-panel-in flex-col items-stretch">
      <Panel className="pointer-events-auto flex max-h-full min-h-0 flex-col">
        <div className="flex h-8 shrink-0 items-center justify-between gap-2 border-b border-hairline px-3">
          <span className="flex items-baseline gap-2">
            <span className="text-micro font-medium uppercase tracking-label text-dim">
              Ground truth
            </span>
            <span className={cn("font-mono text-sm font-medium tabular-nums", qualityText[quality(s / 100)])}>
              {s.toFixed(0)}/100
            </span>
            {report.room && <span className="text-micro text-faint">{report.room}</span>}
          </span>
          <Button
            size="iconSm"
            variant="ghost"
            aria-label={open ? "collapse" : "expand"}
            aria-expanded={open}
            onClick={(e) => { setOpen(!open); e.currentTarget.blur(); }}
          >
            {open ? <ChevronUp className="size-3.5" /> : <ChevronDown className="size-3.5" />}
          </Button>
        </div>
        {open && (
          <div className="min-h-0 flex-1 animate-panel-in overflow-y-auto">
            <PanelBody className="pb-2">
              {det.available && (
                <>
                  <PanelRow label="objects">
                    {det.matched} matched / {det.gt_in_scope} gt · {det.shipped} shipped
                  </PanelRow>
                  <PanelRow label="recall / precision">
                    {pct(det.recall)} · {pct(det.precision)}
                  </PanelRow>
                  {geo.mean_fscore_5cm != null && (
                    <PanelRow label="mean f@5cm">{pct(geo.mean_fscore_5cm)}</PanelRow>
                  )}
                </>
              )}
              <PanelRow label="pose ate">
                {pose.available
                  ? `${((pose.ate_rmse_m ?? 0) * 100).toFixed(2)} cm`
                  : "n/a"}
              </PanelRow>
              {pose.available && pose.source === "dataset_ground_truth" && (
                <p className="mt-1 text-micro text-faint">pipeline used gt poses</p>
              )}
              {!!det.gt_out_of_scope && (
                <p className="mt-1 text-micro text-faint">
                  {det.gt_out_of_scope} gt objects outside pipeline scope (tv/plant/…)
                </p>
              )}
            </PanelBody>
            {objs.length > 0 && (
              <Table>
                <THead>
                  <Tr className="hover:bg-transparent">
                    <Th className="pl-3">object</Th>
                    <Th className="text-right">f@5cm</Th>
                    <Th className="pr-3">verdict</Th>
                  </Tr>
                </THead>
                <TBody>
                  {objs.map((o) => {
                    const f = o.fscore_5cm;
                    const inScene = loaded.has(o.id);
                    return (
                      <Tr
                        key={o.id}
                        selected={o.id === selectedId}
                        onClick={inScene ? () => selectObject(o.id === selectedId ? null : o.id) : undefined}
                        className={inScene ? "cursor-pointer" : undefined}
                      >
                        <Td className={cn("pl-3 font-mono", !inScene && "text-dim")}>{o.id}</Td>
                        <Td numeric>{f == null ? "–" : f.toFixed(2)}</Td>
                        <Td className={cn("pr-3 text-sm", o.matched ? qualityText[quality(f ?? 0)] : "text-poor")}>
                          {o.verdict ?? ""}
                        </Td>
                      </Tr>
                    );
                  })}
                </TBody>
              </Table>
            )}
            {missed.length > 0 && (
              <p className="px-3 py-2 text-micro text-faint">
                missed gt: {missed.map((m) => m.class).join(", ")}
              </p>
            )}
          </div>
        )}
      </Panel>
    </div>
  );
}
