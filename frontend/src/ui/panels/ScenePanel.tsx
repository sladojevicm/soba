// Scene panel (left dock): object list + inspector for the selection.
// List click -> store.selectObject -> viewer highlight -> selection-changed
// -> store -> this panel re-renders. The 3D click path lands in the same
// store field, so selection stays bidirectional with one source of truth.

import { Box } from "lucide-react";
import { selectObject, useSobaStore } from "../../store";
import type { ObjectInfo } from "../../viewer/types";
import { Badge } from "../components/badge";
import { Panel, PanelBody, PanelHeader, PanelRow } from "../components/panel";
import { EmptyState, ErrorState, LoadingState } from "../components/states";
import { cn } from "../lib/utils";

function ObjectRow({ obj, selected }: { obj: ObjectInfo; selected: boolean }) {
  return (
    <button
      onClick={() => selectObject(selected ? null : obj.id)}
      aria-pressed={selected}
      className={cn(
        "flex w-full items-baseline justify-between gap-2 px-3 py-1 text-left text-sm",
        "transition-colors duration-150 ease-out",
        "focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent",
        selected ? "bg-accent/10 text-accent" : "text-text hover:bg-surface-hover"
      )}
    >
      <span className="truncate font-mono">{obj.id}</span>
      <span className={cn("shrink-0 font-mono tabular-nums", selected ? "text-accent" : "text-dim")}>
        {obj.massKg.toFixed(1)} kg
      </span>
    </button>
  );
}

// The gate routed each object: well-observed geometry is kept from the TSDF
// fusion; poorly-observed objects are regenerated from an image crop.
const gateLabel = (o: ObjectInfo) =>
  o.geometrySource === "tsdf" ? "keep · fused tsdf" : "regenerate · image-to-3d";

function Inspector({ obj }: { obj: ObjectInfo }) {
  return (
    <PanelBody>
      <PanelRow label="id">{obj.id}</PanelRow>
      <PanelRow label="class">{obj.cls}</PanelRow>
      <PanelRow label="mass">{obj.massKg.toFixed(2)} kg</PanelRow>
      <PanelRow label="material">{obj.material}</PanelRow>
      <PanelRow label="gate">{gateLabel(obj)}</PanelRow>
      {obj.alignmentMethod !== "n/a" && (
        <PanelRow label="alignment">{obj.alignmentMethod}</PanelRow>
      )}
      {obj.scaleMethod !== "n/a" && (
        <PanelRow label="scale">{obj.scaleMethod}</PanelRow>
      )}
      <PanelRow label="colliders">
        {obj.colliderShape === "box"
          ? "aabb box"
          : `${obj.colliderCount} hull${obj.colliderCount === 1 ? "" : "s"}`}
      </PanelRow>
      <PanelRow label="physics">{obj.physicsOrigin}</PanelRow>
      <div className="mt-2 flex flex-wrap gap-1.5 border-t border-hairline/60 pt-2">
        <Badge>{obj.geometrySource}</Badge>
        <Badge variant="dim">{obj.material}</Badge>
      </div>
      {obj.vlmReasoning && (
        <p className="mt-2 text-sm text-dim">{obj.vlmReasoning}</p>
      )}
    </PanelBody>
  );
}

export function ScenePanel() {
  const phase = useSobaStore((s) => s.phase);
  const error = useSobaStore((s) => s.error);
  const objects = useSobaStore((s) => s.objects);
  const selectedId = useSobaStore((s) => s.selectedId);
  const selected = objects.find((o) => o.id === selectedId) ?? null;

  return (
    <div className="fixed top-2 bottom-10 left-2 z-10 flex w-64 animate-panel-in flex-col gap-2">
      <Panel className="flex min-h-0 flex-1 flex-col">
        <PanelHeader
          title="Scene"
          actions={objects.length > 0 && <Badge variant="dim">{objects.length}</Badge>}
        />
        <div className="min-h-0 flex-1 overflow-y-auto py-1">
          {phase === "boot" && <LoadingState label="fetching scene…" />}
          {phase === "error" && (
            <ErrorState title="Viewer failed to start" detail={error ?? undefined} />
          )}
          {phase === "ready" && objects.length === 0 && (
            <EmptyState
              icon={Box}
              title="No objects yet"
              hint="streaming over sse"
            />
          )}
          {objects.map((o) => (
            <ObjectRow key={o.id} obj={o} selected={o.id === selectedId} />
          ))}
        </div>
      </Panel>
      <Panel className="shrink-0">
        <PanelHeader title="Inspector" />
        {selected ? (
          <Inspector obj={selected} />
        ) : (
          <EmptyState
            title="Nothing selected"
            hint="click an object"
            className="py-5"
          />
        )}
      </Panel>
    </div>
  );
}
