// Story card — the presentation-mode inspector: the pipeline's decision for
// one object in type large enough for a projector. With nothing selected it
// summarises the scene and lists the presenter keys. Reads the store only.

import { ROUTE_NAMES, routeLabel, type Route } from "../../pipeline";
import type { ColorMode } from "../../store";
import { useSobaStore } from "../../store";
import { Kbd } from "../components/kbd";
import { Panel, PanelHeader, PanelRow } from "../components/panel";
import { cn } from "../lib/utils";

const DOT: Record<Route, string> = {
  kept: "bg-route-kept",
  completed: "bg-route-completed",
  generated: "bg-route-generated",
};

function RouteDot({ route }: { route: Route }) {
  return <span className={cn("inline-block size-2 shrink-0 rounded-full", DOT[route])} />;
}

// literal class names: Tailwind only emits utilities it can see in source
const MAT_DOT: Record<string, string> = {
  wood: "bg-mat-wood", metal: "bg-mat-metal", plastic: "bg-mat-plastic", rubber: "bg-mat-rubber",
  glass: "bg-mat-glass", ceramic: "bg-mat-ceramic", fabric: "bg-mat-fabric", paper: "bg-mat-paper",
  stone: "bg-mat-stone", unknown: "bg-mat-unknown",
};
const MODE_HINT: Record<ColorMode, string> = {
  off: "press c to colour the scene by material",
  material: "coloured by material · c again for gate route",
  route: "coloured by gate route · c again to turn off",
};

const KEYS: [string, string][] = [
  ["→", "next object"], ["←", "previous"], ["f", "whole room"],
  ["c", "colour"], ["o", "orbit"], ["r", "reset"], ["p", "panels"],
];

export function StoryCard({ staged }: { staged?: string }) {
  const objects = useSobaStore((s) => s.objects);
  const selectedId = useSobaStore((s) => s.selectedId);
  const pipeline = useSobaStore((s) => s.pipeline);
  const colorMode = useSobaStore((s) => s.colorMode);
  const obj = objects.find((o) => o.id === selectedId) ?? null;
  const pipe = obj ? pipeline?.byId[obj.id] : undefined;

  const assembled = pipeline?.run.assembledIds.length ?? 0;
  const routes = (["kept", "completed", "generated"] as Route[])
    .map((r) => [r, objects.filter((o) => pipeline?.byId[o.id]?.route === r).length] as const)
    .filter(([, n]) => n > 0);
  const materials = [...new Set(objects.map((o) => o.material))]
    .map((m) => [m, objects.filter((o) => o.material === m).length] as const);

  return (
    <div className="fixed bottom-2 left-2 z-10 w-80 animate-panel-in">
      <Panel>
        <PanelHeader
          title={obj ? obj.cls : "Scene"}
          actions={staged && <span className="text-micro uppercase tracking-label text-dim">{staged}</span>}
        />
        {obj ? (
          <div className="p-3">
            <div className="flex items-center gap-2">
              {pipe && <RouteDot route={pipe.route} />}
              <span className="font-mono text-lg text-text-strong">{obj.id}</span>
            </div>
            <div className="mt-1 text-base text-text">{routeLabel(pipe, obj.geometrySource)}</div>
            <div className="mt-2 border-t border-hairline/60 pt-2">
              {pipe?.coverageDeg != null && <PanelRow label="seen from">{pipe.coverageDeg.toFixed(0)}° around</PanelRow>}
              {pipe?.completeness != null && <PanelRow label="completeness">{pipe.completeness.toFixed(2)}</PanelRow>}
              <PanelRow label="mass">{obj.massKg.toFixed(1)} kg</PanelRow>
              <PanelRow label="material">{obj.material}</PanelRow>
              <PanelRow label="collider">
                {obj.colliderShape === "box" ? "box" : `${obj.colliderCount} convex hulls`}
              </PanelRow>
              <PanelRow label="physics from">{obj.physicsOrigin === "vlm" ? "vision-language model" : "lookup table"}</PanelRow>
            </div>
            {obj.vlmReasoning && (
              <p className="mt-2 border-t border-hairline/60 pt-2 text-base text-dim">“{obj.vlmReasoning}”</p>
            )}
          </div>
        ) : (
          <div className="p-3">
            <div className="text-lg text-text-strong">
              {objects.length} objects
              {assembled > objects.length && <span className="text-dim"> of {assembled} reconstructed</span>}
            </div>
            {assembled > objects.length && (
              <p className="mt-1 text-sm text-dim">
                {assembled - objects.length} removed by hand for this demo; the rest is the pipeline's output, unedited.
              </p>
            )}
            <div className="mt-2 flex flex-col gap-1 border-t border-hairline/60 pt-2">
              {colorMode === "material"
                ? materials.map(([m, n]) => (
                    <div key={m} className="flex items-baseline gap-2 text-sm">
                      <span className={cn("inline-block size-2 shrink-0 rounded-full", MAT_DOT[m] ?? MAT_DOT.unknown)} />
                      <span className="font-mono tabular-nums text-text">{n}</span>
                      <span className="text-dim">{m}</span>
                    </div>
                  ))
                : routes.map(([r, n]) => (
                    <div key={r} className="flex items-baseline gap-2 text-sm">
                      <RouteDot route={r} />
                      <span className="font-mono tabular-nums text-text">{n}</span>
                      <span className="text-dim">{ROUTE_NAMES[r]}</span>
                    </div>
                  ))}
              <div className="text-micro uppercase tracking-label text-faint">{MODE_HINT[colorMode]}</div>
            </div>
            <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 border-t border-hairline/60 pt-2">
              {KEYS.map(([k, label]) => (
                <span key={k} className="flex items-center gap-1 text-micro uppercase tracking-label text-faint">
                  <Kbd>{k}</Kbd> {label}
                </span>
              ))}
            </div>
          </div>
        )}
      </Panel>
    </div>
  );
}
