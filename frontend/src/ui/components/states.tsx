// Loading / empty / error presentation. An empty scene is NORMAL, not an
// error — objects stream in over SSE, so the empty state reads as "waiting",
// never as failure. Errors stay in the neutral ramp with strong text: red is
// reserved for eval quality (frontend/CLAUDE.md), so an error announces
// itself through weight and iconography, not color.

import { type ReactNode } from "react";
import { AlertTriangle, type LucideIcon } from "lucide-react";
import { cn } from "../lib/utils";

export function Spinner({ className }: { className?: string }) {
  return (
    <span
      role="status"
      aria-label="loading"
      className={cn(
        "inline-block size-4 animate-spin rounded-full border-2 border-hairline border-t-dim",
        className
      )}
    />
  );
}

export function EmptyState({
  icon: Icon, title, hint, action, className,
}: {
  icon?: LucideIcon;
  title: string;
  hint?: string;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-col items-center gap-2 px-4 py-8 text-center", className)}>
      {Icon && <Icon className="size-5 text-faint" strokeWidth={1.5} />}
      <div className="text-sm text-dim">{title}</div>
      {hint && <div className="text-micro uppercase tracking-label text-faint">{hint}</div>}
      {action && <div className="mt-1">{action}</div>}
    </div>
  );
}

export function LoadingState({ label, className }: { label?: string; className?: string }) {
  return (
    <div className={cn("flex items-center justify-center gap-2 px-4 py-8", className)}>
      <Spinner />
      {label && <span className="text-sm text-dim">{label}</span>}
    </div>
  );
}

export function ErrorState({
  title, detail, action, className,
}: { title: string; detail?: string; action?: ReactNode; className?: string }) {
  return (
    <div className={cn("flex flex-col items-center gap-2 px-4 py-8 text-center", className)}>
      <AlertTriangle className="size-5 text-text-strong" strokeWidth={1.5} />
      <div className="text-sm font-medium text-text-strong">{title}</div>
      {detail && <div className="max-w-xs font-mono text-sm text-dim">{detail}</div>}
      {action && <div className="mt-1">{action}</div>}
    </div>
  );
}
