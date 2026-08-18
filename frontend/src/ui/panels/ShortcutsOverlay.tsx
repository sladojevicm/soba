// Shortcuts overlay — a small edge-docked panel (never a modal: nothing here
// blocks). Toggled by "?" or the toolbar help button; Escape closes it.

import { Kbd } from "../components/kbd";
import { Panel, PanelHeader } from "../components/panel";

const SHORTCUTS: [string, string][] = [
  ["click", "select + wake object"],
  ["drag", "push selected object"],
  ["space", "drop ball"],
  ["f", "frame all objects"],
  ["esc", "deselect / close this"],
  ["?", "toggle shortcuts"],
];

export function ShortcutsOverlay({ open }: { open: boolean }) {
  if (!open) return null;
  return (
    <div className="fixed right-2 bottom-9 z-20 w-64 animate-panel-in">
      <Panel>
        <PanelHeader title="Shortcuts" />
        <div className="flex flex-col gap-1 p-3">
          {SHORTCUTS.map(([key, label]) => (
            <div key={key} className="flex items-center justify-between gap-3">
              <span className="text-sm text-dim">{label}</span>
              <Kbd>{key}</Kbd>
            </div>
          ))}
        </div>
      </Panel>
    </div>
  );
}
