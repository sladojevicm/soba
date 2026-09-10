// Kbd — keyboard hint chip for the toolbar / shortcut overlay.

import { type HTMLAttributes } from "react";
import { cn } from "../lib/utils";

export function Kbd({ className, ...props }: HTMLAttributes<HTMLElement>) {
  return (
    <kbd
      className={cn(
        "inline-flex h-4.5 min-w-4.5 items-center justify-center rounded-sm border border-hairline",
        "bg-surface px-1 font-mono text-micro text-dim",
        className
      )}
      {...props}
    />
  );
}
