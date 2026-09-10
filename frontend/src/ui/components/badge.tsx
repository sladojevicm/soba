// Badge — tiny inline status chip. good/fair/poor are EVAL QUALITY ONLY;
// accent marks selection; everything else is neutral.

import { type HTMLAttributes } from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "../lib/utils";

const badgeVariants = cva(
  "inline-flex items-center rounded-sm px-1.5 py-0.5 text-micro font-medium uppercase tracking-label",
  {
    variants: {
      variant: {
        neutral: "bg-surface-hover text-text",
        dim: "border border-hairline text-dim",
        accent: "bg-accent/15 text-accent",
        good: "bg-good/12 text-good",
        fair: "bg-fair/12 text-fair",
        poor: "bg-poor/12 text-poor",
      },
    },
    defaultVariants: { variant: "neutral" },
  }
);

export interface BadgeProps
  extends HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}
