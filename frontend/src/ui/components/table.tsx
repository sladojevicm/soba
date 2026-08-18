// Table — dense data grid. Micro-label headers, hairline rules at reduced
// opacity, mono + tabular-nums for numeric cells, accent tint for the
// selected row (selection is the accent's only job).

import { type HTMLAttributes, type TdHTMLAttributes, type ThHTMLAttributes } from "react";
import { cn } from "../lib/utils";

export function Table({ className, ...props }: HTMLAttributes<HTMLTableElement>) {
  return <table className={cn("w-full border-collapse text-sm", className)} {...props} />;
}

export function THead(props: HTMLAttributes<HTMLTableSectionElement>) {
  return <thead {...props} />;
}
export function TBody(props: HTMLAttributes<HTMLTableSectionElement>) {
  return <tbody {...props} />;
}

export function Tr({
  className, selected, ...props
}: HTMLAttributes<HTMLTableRowElement> & { selected?: boolean }) {
  return (
    <tr
      aria-selected={selected || undefined}
      className={cn(
        "border-b border-hairline/60 transition-colors duration-150 ease-out",
        "hover:bg-surface-hover/60",
        "focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent",
        selected && "bg-accent/10 hover:bg-accent/10",
        className
      )}
      {...props}
    />
  );
}

export function Th({ className, ...props }: ThHTMLAttributes<HTMLTableCellElement>) {
  return (
    <th
      className={cn(
        "border-b border-hairline px-2 py-1 text-left text-micro font-medium uppercase tracking-label text-dim",
        className
      )}
      {...props}
    />
  );
}

export function Td({
  className, numeric, ...props
}: TdHTMLAttributes<HTMLTableCellElement> & { numeric?: boolean }) {
  return (
    <td
      className={cn(
        "px-2 py-1 text-text",
        numeric && "text-right font-mono tabular-nums",
        className
      )}
      {...props}
    />
  );
}
