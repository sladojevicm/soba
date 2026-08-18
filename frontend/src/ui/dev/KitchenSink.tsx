// Kitchen sink — dev-only design-system reference (/kitchen-sink under
// `npm run dev`; excluded from the committed bundle). Every component and
// state lives here so the system is reviewed BEFORE any product panel is
// built on it (Phase 3 gate).

import { useState } from "react";
import {
  Box, Camera, Crosshair, Maximize, MousePointer2, Pause, Play, RotateCcw,
} from "lucide-react";
import { Button } from "../components/button";
import { Badge } from "../components/badge";
import { Panel, PanelBody, PanelHeader, PanelRow } from "../components/panel";
import { Table, TBody, Td, Th, THead, Tr } from "../components/table";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "../components/tooltip";
import { Slider } from "../components/slider";
import { EmptyState, ErrorState, LoadingState, Spinner } from "../components/states";
import { Kbd } from "../components/kbd";

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-3">
      <h2 className="border-b border-hairline pb-1.5 text-micro font-medium uppercase tracking-label text-dim">
        {title}
      </h2>
      {children}
    </section>
  );
}

function Swatch({ name, cssVar, note }: { name: string; cssVar: string; note?: string }) {
  return (
    <div className="flex items-center gap-2">
      <span
        className="size-5 shrink-0 rounded-sm border border-hairline"
        style={{ backgroundColor: `var(${cssVar})` }}
      />
      <span className="font-mono text-sm text-text">{name}</span>
      {note && <span className="text-micro text-faint">{note}</span>}
    </div>
  );
}

const EVAL_ROWS = [
  { id: "chair_03", cls: "chair", f: 0.96, verdict: "good" as const, mass: "6.1 kg" },
  { id: "chair_07", cls: "chair", f: 0.42, verdict: "fair" as const, mass: "5.8 kg" },
  { id: "dining_table_01", cls: "table", f: 0.0, verdict: "poor" as const, mass: "18.5 kg" },
];

export function KitchenSink() {
  const [sliderVal, setSliderVal] = useState([65]);
  const [selectedRow, setSelectedRow] = useState("chair_07");

  return (
    <TooltipProvider delayDuration={150}>
      <div className="h-full overflow-y-auto bg-bg">
        <div className="mx-auto flex max-w-4xl flex-col gap-8 px-6 py-8">
          <header className="flex items-baseline justify-between">
            <div>
              <h1 className="text-lg font-medium text-text-strong">Soba UI — kitchen sink</h1>
              <p className="mt-1 text-sm text-dim">
                Design-system reference. Dev-only route; not in the committed bundle.
              </p>
            </div>
            <Badge variant="dim">dev</Badge>
          </header>

          <Section title="Color tokens">
            <div className="grid grid-cols-3 gap-x-6 gap-y-2">
              <Swatch name="bg" cssVar="--color-bg" note="canvas" />
              <Swatch name="surface" cssVar="--color-surface" note="panels" />
              <Swatch name="surface-hover" cssVar="--color-surface-hover" note="interaction" />
              <Swatch name="hairline" cssVar="--color-hairline" />
              <Swatch name="text" cssVar="--color-text" />
              <Swatch name="text-strong" cssVar="--color-text-strong" />
              <Swatch name="dim" cssVar="--color-dim" />
              <Swatch name="faint" cssVar="--color-faint" />
              <Swatch name="accent" cssVar="--color-accent" note="selection ONLY" />
              <Swatch name="good" cssVar="--color-good" note="eval only" />
              <Swatch name="fair" cssVar="--color-fair" note="eval only" />
              <Swatch name="poor" cssVar="--color-poor" note="eval only" />
            </div>
          </Section>

          <Section title="Type ramp">
            <div className="flex flex-col gap-2">
              <div className="text-micro uppercase tracking-label text-dim">
                micro 10px — section heads, table headers
              </div>
              <div className="text-sm">sm 12px — dense UI text, table cells, hints</div>
              <div className="text-base">base 13px — body copy and defaults</div>
              <div className="text-lg text-text-strong">lg 15px — sparing emphasis</div>
              <div className="font-mono text-sm tabular-nums">
                mono + tabular-nums 12px — ids, masses, scores: 18.500 kg · 0.42 · 86/100
              </div>
            </div>
          </Section>

          <Section title="Buttons">
            <div className="flex flex-wrap items-center gap-2">
              <Button>Default</Button>
              <Button variant="ghost">Ghost</Button>
              <Button variant="accent">Selected</Button>
              <Button disabled>Disabled</Button>
              <Button size="sm">Small</Button>
              <Button size="sm" variant="ghost">
                <RotateCcw className="size-3.5" /> Reset
              </Button>
              <Button size="icon" aria-label="frame all">
                <Maximize className="size-3.5" />
              </Button>
              <Button size="iconSm" variant="ghost" aria-label="camera">
                <Camera className="size-3.5" />
              </Button>
            </div>
            <p className="text-sm text-faint">
              No danger variant: red is reserved for eval quality. The accent
              variant marks selected/active toggles only.
            </p>
          </Section>

          <Section title="Badges">
            <div className="flex flex-wrap items-center gap-2">
              <Badge>tsdf</Badge>
              <Badge variant="dim">deferred</Badge>
              <Badge variant="accent">selected</Badge>
              <span className="mx-2 h-4 w-px bg-hairline" />
              <Badge variant="good">good</Badge>
              <Badge variant="fair">fair</Badge>
              <Badge variant="poor">poor</Badge>
              <span className="text-micro uppercase tracking-label text-faint">← eval quality only</span>
            </div>
          </Section>

          <Section title="Panel">
            <div className="flex flex-wrap items-start gap-4">
              <Panel className="w-64">
                <PanelHeader
                  title="Inspector"
                  actions={
                    <Button size="iconSm" variant="ghost" aria-label="re-frame">
                      <Crosshair className="size-3.5" />
                    </Button>
                  }
                />
                <PanelBody>
                  <PanelRow label="id">chair_07</PanelRow>
                  <PanelRow label="class">chair</PanelRow>
                  <PanelRow label="mass">5.80 kg</PanelRow>
                  <PanelRow label="material">wood</PanelRow>
                  <PanelRow label="colliders">8 hulls</PanelRow>
                  <div className="mt-2 flex gap-1.5 border-t border-hairline/60 pt-2">
                    <Badge>tsdf</Badge>
                    <Badge variant="fair">fair</Badge>
                  </div>
                </PanelBody>
              </Panel>
              <Panel className="w-64">
                <PanelHeader title="Empty panel" />
                <EmptyState
                  icon={Box}
                  title="No objects yet"
                  hint="streaming over sse"
                />
              </Panel>
            </div>
          </Section>

          <Section title="Table">
            <Panel>
              <Table>
                <THead>
                  <Tr className="hover:bg-transparent">
                    <Th>object</Th>
                    <Th>class</Th>
                    <Th className="text-right">f@5cm</Th>
                    <Th className="text-right">mass</Th>
                    <Th>verdict</Th>
                  </Tr>
                </THead>
                <TBody>
                  {EVAL_ROWS.map((r) => (
                    <Tr
                      key={r.id}
                      selected={selectedRow === r.id}
                      onClick={() => setSelectedRow(r.id)}
                      className="cursor-pointer"
                    >
                      <Td className="font-mono">{r.id}</Td>
                      <Td className="text-dim">{r.cls}</Td>
                      <Td numeric>{r.f.toFixed(2)}</Td>
                      <Td numeric>{r.mass}</Td>
                      <Td><Badge variant={r.verdict}>{r.verdict}</Badge></Td>
                    </Tr>
                  ))}
                </TBody>
              </Table>
            </Panel>
            <p className="text-sm text-faint">
              Row click = selection (accent tint). Numbers right-aligned, mono, tabular.
            </p>
          </Section>

          <Section title="Tooltip">
            <div className="flex items-center gap-6">
              <Tooltip defaultOpen>
                <TooltipTrigger asChild>
                  <Button size="icon" aria-label="select tool">
                    <MousePointer2 className="size-3.5" />
                  </Button>
                </TooltipTrigger>
                <TooltipContent side="bottom" align="start">
                  Select object <Kbd className="ml-1">click</Kbd>
                </TooltipContent>
              </Tooltip>
              <span className="text-sm text-faint">(shown open for review; opens on hover at 150ms)</span>
            </div>
          </Section>

          <Section title="Slider">
            <div className="flex max-w-xs flex-col gap-4">
              <div className="flex items-center gap-3">
                <Slider value={sliderVal} onValueChange={setSliderVal} max={100} step={1} />
                <span className="w-8 text-right font-mono text-sm tabular-nums text-text">
                  {sliderVal[0]}
                </span>
              </div>
              <div className="flex items-center gap-3">
                <Slider defaultValue={[30]} max={100} disabled />
                <span className="w-8 text-right font-mono text-sm tabular-nums text-faint">30</span>
              </div>
            </div>
          </Section>

          <Section title="States">
            <div className="grid grid-cols-3 gap-4">
              <Panel>
                <PanelHeader title="Loading" />
                <LoadingState label="fetching scene…" />
              </Panel>
              <Panel>
                <PanelHeader title="Empty" />
                <EmptyState
                  icon={Box}
                  title="Scene has no objects yet"
                  hint="objects stream in over sse"
                />
              </Panel>
              <Panel>
                <PanelHeader title="Error" />
                <ErrorState
                  title="Physics failed to initialise"
                  detail="RuntimeError: wasm instantiation failed"
                  action={<Button size="sm">Retry</Button>}
                />
              </Panel>
            </div>
            <p className="text-sm text-faint">
              An empty scene is normal, never an error. Errors use weight + icon,
              not red — red belongs to eval quality alone.
            </p>
          </Section>

          <Section title="Keyboard hints">
            <div className="flex items-center gap-4 text-sm text-dim">
              <span className="flex items-center gap-1.5"><Kbd>f</Kbd> frame all</span>
              <span className="flex items-center gap-1.5"><Kbd>space</Kbd> drop ball</span>
              <span className="flex items-center gap-1.5"><Kbd>esc</Kbd> deselect</span>
              <span className="flex items-center gap-1.5">
                <Play className="size-3.5" /><Pause className="size-3.5" />
                <span className="text-faint">icons: lucide, 14px, 1.5px stroke</span>
              </span>
            </div>
          </Section>

          <Section title="Motion">
            <div className="flex items-center gap-3">
              <Spinner />
              <p className="text-sm text-dim">
                120–180ms ease-out on panel open/close and hover only. Tooltip:
                150ms fade+2px rise. Panels: 160ms fade+4px rise. Nothing springy.
              </p>
            </div>
          </Section>
        </div>
      </div>
    </TooltipProvider>
  );
}
