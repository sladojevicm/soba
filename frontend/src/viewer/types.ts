// Types for the scene.json v2.0 contract (spec/scene.schema.json) and the
// viewer's public event/debug surface. Framework-free — no React imports here.

export type Vec3 = [number, number, number];
export type Quat = [number, number, number, number];

export interface Transform {
  translation: Vec3;
  rotation_quat: Quat;
  scale?: number;
}

export interface ColliderSpec {
  shape: "hulls" | "box";
  convex_decomposition?: boolean;
  hull_paths?: string[];
  half_extents?: Vec3;
}

export interface PhysicsSpec {
  mass_kg: number;
  friction: number;
  restitution: number;
  is_rigid: boolean;
}

export interface SourceSpec {
  geometry_source: "tsdf" | "generative";
  alignment_method: string;
  scale_method: string;
  physics_origin: "vlm" | "lookup";
  vlm_reasoning?: string;
}

export interface SceneObject {
  id: string;
  class: string;
  mesh: string;
  transform: Transform;
  collider: ColliderSpec;
  physics: PhysicsSpec;
  material_class: string;
  source: SourceSpec;
  /** optional comparison color (int), set by some eval tooling */
  color?: number;
  /** optional floating label above the object */
  label?: string;
}

export interface SceneJson {
  version: string;
  world?: { gravity: Vec3; up_axis?: string; unit?: string };
  ground?: {
    type?: string;
    normal?: number[];
    y?: number;
    material?: { friction?: number; restitution?: number };
  };
  objects?: SceneObject[];
  camera_pose?: Transform;
}

// ---- viewer events --------------------------------------------------------

export interface ViewerStats {
  fps: number;
  objects: number;
  dynamicBodies: number;
}

export interface ViewerEventMap {
  ready: { objects: number };
  "object-loaded": { id: string; count: number };
  "selection-changed": { id: string | null };
  stats: ViewerStats;
  error: Error;
}

// ---- headless-verification debug handle (shape is FROZEN) -----------------

export interface SobaDebugObject {
  id: string;
  readonly massKg: number;
  readonly bodyType: string;
  readonly position: Vec3;
}

export interface SobaDebug {
  objects: SobaDebugObject[];
  framedAll: boolean;
  screenPos: ((id: string) => [number, number] | null) | null;
}

declare global {
  interface Window {
    __soba?: SobaDebug;
  }
}
