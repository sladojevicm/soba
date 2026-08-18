// Toolbar (top centre) — the two invokable controls. Mouse gestures (click =
// select, drag = push) are passive and live as hints in the status bar.

import { Circle, Maximize } from "lucide-react";
import type { MouseEvent } from "react";
import { dropBall, frameAll } from "../../store";
import { Button } from "../components/button";
import { Kbd } from "../components/kbd";
import { Tooltip, TooltipContent, TooltipTrigger } from "../components/tooltip";

// blur after click so a following Space/Enter drives the viewer, not the button
const run = (fn: () => void) => (e: MouseEvent<HTMLButtonElement>) => {
  fn();
  e.currentTarget.blur();
};

export function Toolbar() {
  return (
    <div className="fixed top-2 left-1/2 z-10 flex -translate-x-1/2 animate-panel-in items-center gap-1 rounded-md border border-hairline bg-surface p-1 shadow-raised">
      <Tooltip>
        <TooltipTrigger asChild>
          <Button size="iconSm" variant="ghost" aria-label="frame all objects" onClick={run(frameAll)}>
            <Maximize className="size-3.5" />
          </Button>
        </TooltipTrigger>
        <TooltipContent side="bottom">
          Frame all <Kbd className="ml-1">f</Kbd>
        </TooltipContent>
      </Tooltip>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button size="iconSm" variant="ghost" aria-label="drop ball" onClick={run(dropBall)}>
            <Circle className="size-3.5" />
          </Button>
        </TooltipTrigger>
        <TooltipContent side="bottom">
          Drop ball <Kbd className="ml-1">space</Kbd>
        </TooltipContent>
      </Tooltip>
    </div>
  );
}
