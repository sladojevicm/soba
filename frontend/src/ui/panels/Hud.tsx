// HUD — Phase-2 parity port of the pre-React status box. Same id, same DOM
// shape, same text (styles in index.css are the originals, verbatim).

import { useSobaStore } from "../../store";

export function Hud() {
  const phase = useSobaStore((s) => s.phase);
  const error = useSobaStore((s) => s.error);
  const objectCount = useSobaStore((s) => s.objectCount);

  if (phase === "boot") return <div id="hud">initialising physics…</div>;
  if (phase === "error") return <div id="hud">error: {error}</div>;
  return (
    <div id="hud">
      <b>Soba viewer</b>{"\n"}
      {`objects: ${objectCount}`}{"\n"}
      <span className="key">click</span>{" select · "}
      <span className="key">drag</span>{" push · "}
      <span className="key">space</span>{" drop ball · "}
      <span className="key">f</span>{" frame all"}
    </div>
  );
}
