// Button — quiet by default; the accent variant is for SELECTED/active toggle
// states only (accent = selection, frontend/CLAUDE.md). There is deliberately
// no red/danger variant: semantic colors are reserved for eval quality.

import { forwardRef, type ButtonHTMLAttributes } from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "../lib/utils";

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-1.5 rounded-sm text-sm font-medium " +
    "whitespace-nowrap select-none transition-colors duration-150 ease-out " +
    "focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent " +
    "disabled:pointer-events-none disabled:opacity-50",
  {
    variants: {
      variant: {
        default: "bg-surface text-text border border-hairline hover:bg-surface-hover hover:text-text-strong",
        ghost: "text-dim hover:text-text hover:bg-surface-hover",
        accent: "bg-accent/15 text-accent border border-hairline",
      },
      size: {
        md: "h-7 px-2.5",
        sm: "h-6 px-2",
        icon: "size-7",
        iconSm: "size-6",
      },
    },
    defaultVariants: { variant: "default", size: "md" },
  }
);

export interface ButtonProps
  extends ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, ...props }, ref) => (
    <button ref={ref} className={cn(buttonVariants({ variant, size }), className)} {...props} />
  )
);
Button.displayName = "Button";
