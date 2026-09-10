// Panel — the edge-docked surface. Exactly ONE elevation level above the
// canvas (bg-surface + shadow-raised); nothing stacks on top of a panel
// except tooltips.

import { type HTMLAttributes, type ReactNode } from "react";
import { cn } from "../lib/utils";

export function Panel({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "rounded-md border border-hairline bg-surface shadow-raised",
        className
      )}
      {...props}
    />
  );
}

// Uppercase micro-label section head with optional right-aligned actions.
export function PanelHeader({
  title, actions, className,
}: { title: string; actions?: ReactNode; className?: string }) {
  return (
    <div className={cn("flex h-8 items-center justify-between gap-2 border-b border-hairline px-3", className)}>
      <span className="text-micro font-medium uppercase tracking-label text-dim">{title}</span>
      {actions && <span className="flex items-center gap-1">{actions}</span>}
    </div>
  );
}

export function PanelBody({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("p-3", className)} {...props} />;
}

// Key/value row — the inspector staple. Values are mono + tabular.
export function PanelRow({
  label, children, className,
}: { label: string; children: ReactNode; className?: string }) {
  return (
    <div className={cn("flex items-baseline justify-between gap-3 py-0.5", className)}>
      <span className="text-sm text-dim">{label}</span>
      <span className="text-sm font-mono tabular-nums text-text text-right">{children}</span>
    </div>
  );
}
