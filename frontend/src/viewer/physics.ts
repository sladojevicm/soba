// Rapier world / body / collider construction. The load-bearing correctness
// points (plan §16, frontend/CLAUDE.md) all live in this file:
//   * mass is set ON THE RIGID-BODY DESC, before createRigidBody (fix P1/D5) —
//     setAdditionalMass after creation acts on a stale desc and is ignored,
//     which would silently drop the computed mass.
//   * hull colliders use the CoACD parts with DENSITY 0, so Rapier never
//     re-derives mass from hull geometry (a hollow object would come out wrong).
//   * gravity + ground.y come FROM scene.json (fix Z-J / K4), not hardcoded.

import RAPIER, { type RigidBody, type World } from "@dimforge/rapier3d-compat";
import type { SceneJson, SceneObject } from "./types";

// Gravity FROM scene.json (fix Z-J), not hardcoded. Assert the up axis.
export function initWorld(sceneJson: SceneJson): World {
  const g = (sceneJson.world && sceneJson.world.gravity) || [0, -9.81, 0];
  if (sceneJson.world && sceneJson.world.up_axis && sceneJson.world.up_axis !== "y") {
    console.warn("scene up_axis is not 'y'; renderer assumes Y-up");
  }
  const world = new RAPIER.World({ x: g[0], y: g[1], z: g[2] });
  world.timestep = 1 / 60; // fixed dt (plan §16 step 5)
  return world;
}

// Ground from scene.json ground.y (fix K4). cuboid() takes HALF-extents (Z6),
// so cuboid(50,0.1,50) is 100 x 0.2 x 100 m; centre it so the TOP face sits at
// ground.y -> centre.y = ground.y - 0.1.
export function createGroundCollider(world: World, sceneJson: SceneJson): void {
  const ground = sceneJson.ground || { y: 0, material: {} };
  const gy = ground.y || 0;
  const gmat = ground.material || { friction: 0.85, restitution: 0.1 };
  const groundBody = world.createRigidBody(
    RAPIER.RigidBodyDesc.fixed().setTranslation(0, gy - 0.1, 0)
  );
  world.createCollider(
    RAPIER.ColliderDesc.cuboid(50, 0.1, 50)
      .setFriction(gmat.friction ?? 0.85)
      .setRestitution(gmat.restitution ?? 0.1),
    groundBody
  );
}

// Rigid body for one scene object. Objects start FIXED (static) so a
// reconstructed room LOADS STABLE — the meshes are placed at their observed
// positions and are often sized by a class prior, so several can overlap; if
// they were all dynamic on load, Rapier ejects the interpenetrations and the
// whole scene EXPLODES. Each body becomes dynamic on demand when clicked.
// Mass is set on the desc (fix P1/D5) and applies once the body turns dynamic.
export function createObjectBody(world: World, entry: SceneObject): RigidBody {
  const [tx, ty, tz] = entry.transform.translation;
  const q = entry.transform.rotation_quat; // [x,y,z,w]
  const desc = RAPIER.RigidBodyDesc.fixed()
    .setTranslation(tx, ty, tz)
    .setRotation({ x: q[0], y: q[1], z: q[2], w: q[3] })
    .setAdditionalMass(entry.physics.mass_kg);
  return world.createRigidBody(desc);
}

// Colliders. Tiers 2-4: CoACD hulls (density 0). Tier 1: AABB box.
// Hull GLBs are loaded by the caller (async); this attaches the geometry.
export function attachColliders(
  world: World, body: RigidBody, entry: SceneObject, hullVerts: Float32Array[]
): void {
  const phys = entry.physics;
  const col = entry.collider;
  if (col.shape === "box") {
    const [hx, hy, hz] = col.half_extents!;
    const cdesc = RAPIER.ColliderDesc.cuboid(hx, hy, hz)
      .setDensity(0)
      .setFriction(phys.friction)
      .setRestitution(phys.restitution);
    world.createCollider(cdesc, body);
  } else {
    for (const verts of hullVerts) {
      const cdesc = RAPIER.ColliderDesc.convexHull(verts);
      if (!cdesc) { console.warn("degenerate hull for", entry.id); continue; }
      cdesc.setDensity(0).setFriction(phys.friction).setRestitution(phys.restitution);
      world.createCollider(cdesc, body);
    }
  }
}
