// Conference demo flow at /?demo: upload -> staged processing -> the viewer.
// Entirely client-side. The viewer (and with it the window-level space / f
// keys) is not mounted until the reveal, so the earlier screens own the
// keyboard. The plain viewer at / is untouched (scripts/verify_browser.js).

import { useCallback, useEffect, useState } from "react";
import { fetchPipeline, type PipelineData } from "../../pipeline";
import { frameAllNow, setAutoOrbit, setPresentation, useSobaStore } from "../../store";
import App from "../App";
import { ProcessingScreen } from "./ProcessingScreen";
import { StagedBanner } from "./StagedBanner";
import { UploadScreen } from "./UploadScreen";
import { formatRunDate } from "./format";

type Phase = "upload" | "processing" | "reveal";

export default function DemoApp() {
  const [phase, setPhase] = useState<Phase>("upload");
  const [step, setStep] = useState(0);
  const [pipeline, setPipeline] = useState<PipelineData | null>(null);

  // recorded numbers for the staged screens; the viewer is not mounted yet
  useEffect(() => { void fetchPipeline().then(setPipeline); }, []);

  const toProcessing = useCallback(() => { setStep(0); setPhase("processing"); }, []);
  const toReveal = useCallback(() => setPhase("reveal"), []);

  // The whole demo is projector-sized; the reveal opens in presentation mode
  // with a slow orbit that stops as soon as the presenter touches the camera.
  useEffect(() => {
    setPresentation(true);
    document.documentElement.dataset.presentation = "on";
  }, []);
  useEffect(() => {
    if (phase !== "reveal") return;
    return useSobaStore.subscribe((s, prev) => {
      if (s.phase === "ready" && prev.phase !== "ready") {
        // the capture pose usually sits inside the furniture: open on the room
        frameAllNow();
        setAutoOrbit(true);
      }
    });
  }, [phase]);

  if (phase === "reveal") {
    const when = pipeline?.run.startedAt ? formatRunDate(pipeline.run.startedAt) : null;
    return <App staged={when ? `recorded ${when}` : "recorded run"} />;
  }
  return (
    <div className="flex h-full flex-col bg-bg">
      {phase === "processing" && <StagedBanner run={pipeline?.run ?? null} />}
      {phase === "upload"
        ? <UploadScreen onContinue={toProcessing} />
        : <ProcessingScreen run={pipeline?.run ?? null} step={step} onStep={setStep} onDone={toReveal} />}
    </div>
  );
}
