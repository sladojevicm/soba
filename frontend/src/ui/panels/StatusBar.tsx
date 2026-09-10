// Status bar (bottom edge): scene state, live stats (4 Hz — the only
// continuously updating React surface), current selection, gesture hints.

import { useSobaStore } from "../../store";
import { Spinner } from "../components/states";

function Sep() {
  return <span className="h-3.5 w-px bg-hairline" />;
}

export function StatusBar() {
  const phase = useSobaStore((s) => s.phase);
  const objectCount = useSobaStore((s) => s.objects.length);
  const selectedId = useSobaStore((s) => s.selectedId);
  const stats = useSobaStore((s) => s.stats);

  return (
    <div className="fixed inset-x-0 bottom-0 z-10 flex h-7 items-center gap-3 border-t border-hairline bg-surface px-3 text-sm">
      {phase === "boot" && (
        <span className="flex items-center gap-2 text-dim">
          <Spinner className="size-3" /> loading scene…
        </span>
      )}
      {phase === "error" && (
        <span className="font-medium text-text-strong">viewer error</span>
      )}
      {phase === "ready" && (
        <span className="text-dim">
          objects <span className="font-mono tabular-nums text-text">{objectCount}</span>
        </span>
      )}
      <Sep />
      <span className="text-dim">
        fps <span className="font-mono tabular-nums text-text">{stats ? stats.fps : "–"}</span>
      </span>
      <span className="text-dim">
        dynamic <span className="font-mono tabular-nums text-text">{stats ? stats.dynamicBodies : "–"}</span>
      </span>
      <Sep />
      <span className="text-dim">
        selected{" "}
        {selectedId
          ? <span className="font-mono text-accent">{selectedId}</span>
          : <span className="text-faint">—</span>}
      </span>
      <span className="ml-auto text-micro uppercase tracking-label text-faint">
        <span className="hidden lg:inline">click select · drag push · space ball · f frame · </span>
        ? shortcuts
      </span>
    </div>
  );
}
