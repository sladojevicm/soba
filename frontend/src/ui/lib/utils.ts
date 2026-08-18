// shadcn/ui utility — class merging for component variants.
//
// tailwind-merge must be taught the custom color tokens: it cannot classify
// unknown classes like `text-dim` vs `text-text`, so without this they would
// both be kept and CSS order (not call order) would win the conflict.
import { clsx, type ClassValue } from "clsx";
import { extendTailwindMerge } from "tailwind-merge";

const twMerge = extendTailwindMerge({
  extend: {
    theme: {
      color: [
        "bg", "surface", "surface-hover", "hairline",
        "text", "text-strong", "dim", "faint",
        "accent", "good", "fair", "poor",
      ],
      text: ["micro", "sm", "base", "lg"],
    },
  },
});

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
