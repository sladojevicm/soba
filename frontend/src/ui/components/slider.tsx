// Slider — Radix primitive. Neutral throughout: the accent is reserved for
// selection, so track/range/thumb stay in the gray ramp.

import * as SliderPrimitive from "@radix-ui/react-slider";
import { type ComponentPropsWithoutRef, type ElementRef, forwardRef } from "react";
import { cn } from "../lib/utils";

export const Slider = forwardRef<
  ElementRef<typeof SliderPrimitive.Root>,
  ComponentPropsWithoutRef<typeof SliderPrimitive.Root>
>(({ className, ...props }, ref) => (
  <SliderPrimitive.Root
    ref={ref}
    className={cn(
      "relative flex w-full touch-none select-none items-center py-1.5",
      "data-[disabled]:opacity-50",
      className
    )}
    {...props}
  >
    <SliderPrimitive.Track className="relative h-0.5 w-full grow overflow-hidden rounded-full bg-hairline">
      <SliderPrimitive.Range className="absolute h-full bg-dim" />
    </SliderPrimitive.Track>
    <SliderPrimitive.Thumb
      className={cn(
        "block size-3 rounded-full bg-text transition-colors duration-150 ease-out",
        "hover:bg-text-strong",
        "focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
      )}
    />
  </SliderPrimitive.Root>
));
Slider.displayName = "Slider";
