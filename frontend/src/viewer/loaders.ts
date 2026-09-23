// Mesh-loading helpers — framework-free, no DOM beyond an offscreen canvas
// used as a sprite texture (never appended to the document).

import * as THREE from "three";
import type { GLTF } from "three/addons/loaders/GLTFLoader.js";

// Base-path-aware scene URL. The viewer is served both at `/` (single-scene
// mode: /scene.json, /meshes/..., /events) and under a job prefix
// (`/jobs/<id>/scene.json`, ...). Everything the viewer fetches goes through
// here so the page location decides which scene the routes resolve to; the
// bundle's own assets stay absolute (/assets/...) and are unaffected.
const JOB_PREFIX_RE = /^\/jobs\/[^/]+/;
export function sceneUrl(path: string): string {
  const m = JOB_PREFIX_RE.exec(window.location.pathname);
  return (m ? m[0] : "") + (path.startsWith("/") ? path : "/" + path);
}

// Pull a flat Float32Array of world-space vertices out of a loaded GLB (bakes
// any node transform in, so Rapier's convex hull matches what is rendered).
export function glbVertices(gltf: GLTF): Float32Array {
  const pts: number[] = [];
  gltf.scene.updateMatrixWorld(true);
  gltf.scene.traverse((o) => {
    if ((o as THREE.Mesh).isMesh && (o as THREE.Mesh).geometry) {
      const mesh = o as THREE.Mesh;
      const pos = mesh.geometry.attributes.position;
      const v = new THREE.Vector3();
      for (let i = 0; i < pos.count; i++) {
        v.fromBufferAttribute(pos, i).applyMatrix4(mesh.matrixWorld);
        pts.push(v.x, v.y, v.z);
      }
    }
  });
  return new Float32Array(pts);
}

export function enableShadows(obj: THREE.Object3D): void {
  obj.traverse((o) => {
    if ((o as THREE.Mesh).isMesh) { o.castShadow = true; o.receiveShadow = true; }
  });
}

// Floating text label (canvas-texture sprite) that always faces the camera.
export function makeLabel(text: string, hex: number): THREE.Sprite {
  const fs = 52, pad = 22;
  const c = document.createElement("canvas");
  const ctx = c.getContext("2d")!;
  ctx.font = `bold ${fs}px sans-serif`;
  c.width = Math.ceil(ctx.measureText(text).width) + pad * 2;
  c.height = fs + pad;
  ctx.font = `bold ${fs}px sans-serif`;
  ctx.fillStyle = "rgba(18,20,26,0.88)";
  ctx.fillRect(0, 0, c.width, c.height);
  ctx.fillStyle = "#" + ("000000" + (hex >>> 0).toString(16)).slice(-6);
  ctx.fillRect(0, c.height - 8, c.width, 8); // color underline = model color
  ctx.fillStyle = "#fff";
  ctx.textBaseline = "middle";
  ctx.fillText(text, pad, (c.height - 8) / 2 + 2);
  const sprite = new THREE.Sprite(new THREE.SpriteMaterial({
    map: new THREE.CanvasTexture(c), depthTest: false, transparent: true,
  }));
  const s = 0.0045;
  sprite.scale.set(c.width * s, c.height * s, 1);
  return sprite;
}
