// The honesty line of the demo. Permanent wherever staged content is on
// screen: never dismissible, never hidden by presentation mode.

import type { RunSummary } from "../../pipeline";
import { formatDuration, formatRunDate } from "./format";

export function StagedBanner({ run }: { run: RunSummary | null }) {
  const when = run?.startedAt ? formatRunDate(run.startedAt) : null;
  const took = run?.totalSeconds != null ? formatDuration(run.totalSeconds) : null;
  return (
    <div
      role="note"
      className="flex flex-wrap items-baseline justify-center gap-x-2 border-b border-hairline bg-surface px-4 py-2 text-center text-base text-dim"
    >
      <span className="text-micro font-medium uppercase tracking-label text-text-strong">Staged replay</span>
      <span>
        {when ? `of a run recorded on ${when}` : "of a recorded run"}
        {took ? `. The real run took ${took} on one GPU` : ". A real run takes several minutes on a GPU"}
        . Nothing is computed live.
      </span>
    </div>
  );
}
