// Upload screen — the thing the presenter actually touches on stage. The file
// is only DESCRIBED (name, size, a local video thumbnail): it never leaves the
// browser and no request is made. The copy says "RGB-D capture" on purpose:
// the verified pipeline takes depth + masks, not a plain phone video.

import { useEffect, useRef, useState, type DragEvent } from "react";
import { FileVideo, Package, Upload } from "lucide-react";
import { Button } from "../components/button";
import { Kbd } from "../components/kbd";
import { Panel } from "../components/panel";
import { cn } from "../lib/utils";
import { formatBytes } from "./format";

const ACCEPT = "video/*,.zip,.tar,.gz,.tgz";

export function UploadScreen({ onContinue }: { onContinue: () => void }) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [over, setOver] = useState(false);
  const [videoUrl, setVideoUrl] = useState<string | null>(null);

  // local object URL for a video thumbnail; revoked when the file changes
  useEffect(() => {
    if (!file || !file.type.startsWith("video/")) { setVideoUrl(null); return; }
    const url = URL.createObjectURL(file);
    setVideoUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Enter" && file) { e.preventDefault(); onContinue(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [file, onContinue]);

  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setOver(false);
    const f = e.dataTransfer.files?.[0];
    if (f) setFile(f);
  };

  const Icon = file && !file.type.startsWith("video/") ? Package : FileVideo;

  return (
    <div className="flex h-full overflow-y-auto px-6 py-4">
    <div className="m-auto flex w-full flex-col items-center gap-6">
      <div className="text-center">
        <h1 className="text-2xl font-medium text-text-strong">Soba</h1>
        <p className="mt-1 text-xl text-dim">an RGB-D capture of a room in, a physics-enabled 3D scene out</p>
      </div>

      <Panel
        onDragOver={(e) => { e.preventDefault(); setOver(true); }}
        onDragLeave={() => setOver(false)}
        onDrop={onDrop}
        className={cn(
          "flex w-full max-w-2xl flex-col items-center gap-4 border-dashed p-8 transition-colors duration-150 ease-out",
          over && "bg-surface-hover"
        )}
      >
        {videoUrl ? (
          <video src={videoUrl} muted loop autoPlay playsInline className="max-h-64 w-full rounded-sm bg-bg object-contain" />
        ) : (
          <Icon className="size-10 text-faint" strokeWidth={1.25} />
        )}

        {file ? (
          <div className="text-center">
            <div className="font-mono text-xl text-text-strong">{file.name}</div>
            <div className="mt-1 font-mono text-base tabular-nums text-dim">
              {formatBytes(file.size)}{file.type ? ` · ${file.type}` : ""}
            </div>
          </div>
        ) : (
          <div className="text-center">
            <div className="text-xl text-text">Drop an RGB-D capture here</div>
            <div className="mt-1 text-base text-dim">a capture video or a bundle archive (frames, depth, masks)</div>
          </div>
        )}

        <input
          ref={inputRef}
          type="file"
          accept={ACCEPT}
          className="hidden"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
        />
        <div className="flex items-center gap-2">
          <Button onClick={() => inputRef.current?.click()}>
            <Upload className="size-3.5" /> {file ? "Choose another" : "Choose a file"}
          </Button>
          {file && (
            <Button variant="accent" onClick={onContinue}>
              Process capture <Kbd className="ml-1">enter</Kbd>
            </Button>
          )}
        </div>
      </Panel>

      <div className="flex flex-col items-center gap-2">
        <p className="max-w-2xl text-center text-base text-faint">
          Demo mode: the file stays in this browser. Nothing is uploaded and nothing is computed live.
        </p>
        {!file && (
          <Button variant="ghost" size="sm" onClick={onContinue}>continue without a file</Button>
        )}
      </div>
    </div>
    </div>
  );
}
