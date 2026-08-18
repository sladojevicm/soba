// Camera framing (DEBT A): frame the WHOLE ROOM, not one object.
// Pure math — no viewer state, no DOM.

import * as THREE from "three";

// Bbox of all selectable meshes; null while the scene is empty.
export function boundsOf(scene: THREE.Scene, meshes: THREE.Object3D[]): THREE.Box3 | null {
  if (!meshes.length) return null;
  scene.updateMatrixWorld(true); // objects may not have rendered yet
  const box = new THREE.Box3();
  for (const m of meshes) box.expandByObject(m);
  return box.isEmpty() ? null : box;
}

// Camera placement that fits `box` into the view: target at the bbox centre,
// camera along a pleasant 35°-elevation diagonal, distance chosen so the
// bounding sphere fits the narrower FOV axis with ~15% margin.
export function framePlacement(
  box: THREE.Box3, fovDeg: number, aspect: number
): { center: THREE.Vector3; position: THREE.Vector3 } {
  const center = box.getCenter(new THREE.Vector3());
  const sphere = box.getBoundingSphere(new THREE.Sphere());
  const vFov = THREE.MathUtils.degToRad(fovDeg);
  const hFov = 2 * Math.atan(Math.tan(vFov / 2) * aspect);
  const fov = Math.min(vFov, hFov);
  const dist = Math.max(0.5, (sphere.radius * 1.15) / Math.sin(fov / 2));
  const elev = THREE.MathUtils.degToRad(35);
  const azim = THREE.MathUtils.degToRad(45);
  const dir = new THREE.Vector3(
    Math.cos(elev) * Math.sin(azim),
    Math.sin(elev),
    Math.cos(elev) * Math.cos(azim)
  );
  const position = center.clone().addScaledVector(dir, dist);
  return { center, position };
}
