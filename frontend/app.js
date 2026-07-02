// Step 11 — Browser physics + rendering (Phase 11).
//
// Three.js renders the assembled scene; Rapier (WASM) simulates it. The design
// is plan §16. The load-bearing correctness points it calls out:
//   * mass is set ON THE RIGID-BODY DESC, before createRigidBody (fix P1/D5) —
//     setAdditionalMass after creation acts on a stale desc and is ignored,
//     which would silently drop the computed mass.
//   * hull colliders use the CoACD parts with DENSITY 0, so Rapier never
//     re-derives mass from hull geometry (a hollow object would come out wrong).
//   * gravity + ground.y come FROM scene.json (fix Z-J / K4), not hardcoded.
//   * each object_added SSE event triggers a re-GET of /scene.json and a lookup
//     by id (fix Z-C); the event carries only the id.
//   * the camera starts from scene.json camera_pose when present (fix W5); the
//     view then frames the bbox of ALL objects (auto until the user interacts,
//     "f" to re-frame any time) so a full room never looks like one lone object.

import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import RAPIER from "@dimforge/rapier3d-compat";

const hud = document.getElementById("hud");
const log = (msg) => { hud.innerHTML = msg; };

// ---------------------------------------------------------------------------
// Three.js setup
// ---------------------------------------------------------------------------
const canvas = document.getElementById("c");
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.setSize(window.innerWidth, window.innerHeight);
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.0;

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x15171c);

const camera = new THREE.PerspectiveCamera(
  55, window.innerWidth / window.innerHeight, 0.05, 200
);
camera.position.set(3, 2.4, 3); // default; overridden by camera_pose below

const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.dampingFactor = 0.08;

// Lights: soft hemisphere fill + a shadow-casting key light.
scene.add(new THREE.HemisphereLight(0xbfd0e6, 0x2b2620, 0.9));
const key = new THREE.DirectionalLight(0xffffff, 2.2);
key.position.set(4, 8, 5);
key.castShadow = true;
key.shadow.mapSize.set(2048, 2048);
key.shadow.camera.near = 0.5;
key.shadow.camera.far = 40;
key.shadow.camera.left = -10; key.shadow.camera.right = 10;
key.shadow.camera.top = 10; key.shadow.camera.bottom = -10;
key.shadow.bias = -0.0004;
scene.add(key);

window.addEventListener("resize", () => {
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
});

const gltfLoader = new GLTFLoader();

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
let world = null;                 // RAPIER.World
const syncMap = new Map();        // rigidBody -> THREE.Object3D
const bodyMeshes = [];            // selectable THREE meshes (for raycasting)
const meshToEntry = new Map();    // THREE.Object3D -> scene.json object entry
const loadedIds = new Set();      // ids already added
const tempBalls = [];             // {body, mesh, dieAt}
let hasCameraPose = false;        // scene.json camera_pose wins initial placement (W5)
let userInteracted = false;       // stop auto-framing once the user touches the camera

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
async function getScene() {
  const r = await fetch("/scene.json", { cache: "no-store" });
  return r.json();
}

// Pull a flat Float32Array of world-space vertices out of a loaded GLB (bakes
// any node transform in, so Rapier's convex hull matches what is rendered).
function glbVertices(gltf) {
  const pts = [];
  gltf.scene.updateMatrixWorld(true);
  gltf.scene.traverse((o) => {
    if (o.isMesh && o.geometry) {
      const pos = o.geometry.attributes.position;
      const v = new THREE.Vector3();
      for (let i = 0; i < pos.count; i++) {
        v.fromBufferAttribute(pos, i).applyMatrix4(o.matrixWorld);
        pts.push(v.x, v.y, v.z);
      }
    }
  });
  return new Float32Array(pts);
}

function enableShadows(obj) {
  obj.traverse((o) => {
    if (o.isMesh) { o.castShadow = true; o.receiveShadow = true; }
  });
}

// ---------------------------------------------------------------------------
// Camera framing (DEBT A): frame the WHOLE ROOM, not one object.
// ---------------------------------------------------------------------------
function sceneBounds() {
  if (!bodyMeshes.length) return null;
  scene.updateMatrixWorld(true); // objects may not have rendered yet
  const box = new THREE.Box3();
  for (const m of bodyMeshes) box.expandByObject(m);
  return box.isEmpty() ? null : box;
}

// Fit the bbox of ALL loaded meshes into the view: OrbitControls target at the
// bbox centre, camera along a pleasant 35°-elevation diagonal, distance chosen
// so the bounding sphere fits the narrower FOV axis with ~15% margin.
function frameAll() {
  const box = sceneBounds();
  if (!box) return;
  const center = box.getCenter(new THREE.Vector3());
  const sphere = box.getBoundingSphere(new THREE.Sphere());
  const vFov = THREE.MathUtils.degToRad(camera.fov);
  const hFov = 2 * Math.atan(Math.tan(vFov / 2) * camera.aspect);
  const fov = Math.min(vFov, hFov);
  const dist = Math.max(0.5, (sphere.radius * 1.15) / Math.sin(fov / 2));
  const elev = THREE.MathUtils.degToRad(35);
  const azim = THREE.MathUtils.degToRad(45);
  const dir = new THREE.Vector3(
    Math.cos(elev) * Math.sin(azim),
    Math.sin(elev),
    Math.cos(elev) * Math.cos(azim)
  );
  camera.position.copy(center).addScaledVector(dir, dist);
  controls.target.copy(center);
  controls.update();
}

// Called after each object loads. Auto-frame only until the user first touches
// the camera (don't fight the user). When scene.json has a camera_pose it wins
// the INITIAL camera position (fix W5) — then we only aim the controls target
// at the whole scene; "f" re-frames fully at any time.
function maybeAutoFrame() {
  if (userInteracted) return;
  if (hasCameraPose) {
    const box = sceneBounds();
    if (box) { controls.target.copy(box.getCenter(new THREE.Vector3())); controls.update(); }
  } else {
    frameAll();
  }
}

// Floating text label (canvas-texture sprite) that always faces the camera.
function makeLabel(text, hex) {
  const fs = 52, pad = 22;
  const c = document.createElement("canvas");
  const ctx = c.getContext("2d");
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

// ---------------------------------------------------------------------------
// Object loading (one object_added event)
// ---------------------------------------------------------------------------
async function addObject(id, sceneJson) {
  if (loadedIds.has(id)) return;
  const entry = (sceneJson.objects || []).find((o) => o.id === id);
  if (!entry) { console.warn("no scene.json entry for", id); return; }
  loadedIds.add(id);

  const t = entry.transform;
  const [tx, ty, tz] = t.translation;
  const q = t.rotation_quat; // [x,y,z,w]

  // 1. Render mesh (do NOT collapse to one mesh — that strips PBR materials).
  const gltf = await gltfLoader.loadAsync(`/meshes/${id}.glb`);
  const obj3d = gltf.scene;
  // Render DOUBLE-SIDED: these are TSDF / marching-cubes / completion meshes whose
  // triangle winding isn't perfectly consistent (e.g. Taubin smoothing can flip
  // normals on thin features), so single-sided (default) culls the back-facing
  // ones and they read as SEE-THROUGH HOLES. DoubleSide renders both faces -> the
  // surface looks solid regardless of winding. Also apply the optional per-object
  // comparison color.
  const hasColor = entry.color !== undefined && entry.color !== null;
  obj3d.traverse((o) => {
    if (!o.isMesh) return;
    if (hasColor) {
      o.material = new THREE.MeshStandardMaterial({
        color: entry.color, roughness: 0.55, metalness: 0.0,
      });
    }
    if (o.material) o.material.side = THREE.DoubleSide;
  });
  enableShadows(obj3d);
  obj3d.position.set(tx, ty, tz);
  obj3d.quaternion.set(q[0], q[1], q[2], q[3]);
  scene.add(obj3d);
  bodyMeshes.push(obj3d);
  meshToEntry.set(obj3d, entry);

  // floating label above the object (model · object), color-underlined
  if (entry.label) {
    const hy = entry.collider?.half_extents?.[1] ?? 0.5;
    const label = makeLabel(entry.label, entry.color ?? 0xffffff);
    label.position.set(tx, ty + hy + 0.45, tz);
    scene.add(label);
  }

  // 5. Rigid body. Objects start FIXED (static) so a reconstructed room LOADS
  // STABLE — the meshes are placed at their observed positions and are often
  // sized by a class prior, so several can overlap; if they were all dynamic on
  // load, Rapier ejects the interpenetrations and the whole scene EXPLODES. Each
  // body becomes dynamic on demand when you click it (see setupInteraction), so
  // you can still push it / drop the ball on it. Mass is set on the desc (fix
  // P1/D5) and applies once the body turns dynamic.
  const phys = entry.physics;
  const desc = RAPIER.RigidBodyDesc.fixed()
    .setTranslation(tx, ty, tz)
    .setRotation({ x: q[0], y: q[1], z: q[2], w: q[3] })
    .setAdditionalMass(phys.mass_kg);
  const body = world.createRigidBody(desc);

  // 6. Colliders. Tiers 2-4: CoACD hulls (density 0). Tier 1: AABB box.
  const col = entry.collider;
  if (col.shape === "box") {
    const [hx, hy, hz] = col.half_extents;
    const cdesc = RAPIER.ColliderDesc.cuboid(hx, hy, hz)
      .setDensity(0)
      .setFriction(phys.friction)
      .setRestitution(phys.restitution);
    world.createCollider(cdesc, body);
  } else {
    // hull_paths are scene-relative ("hulls/{id}_{i}.glb"); the server remaps
    // /hulls/{id}_{i}.glb -> objects/{id}/hulls/{id}_{i}.glb.
    const hullGltfs = await Promise.all(
      col.hull_paths.map((p) => gltfLoader.loadAsync("/" + p))
    );
    for (const hg of hullGltfs) {
      const verts = glbVertices(hg);
      const cdesc = RAPIER.ColliderDesc.convexHull(verts);
      if (!cdesc) { console.warn("degenerate hull for", id); continue; }
      cdesc.setDensity(0).setFriction(phys.friction).setRestitution(phys.restitution);
      world.createCollider(cdesc, body);
    }
  }

  syncMap.set(body, obj3d);

  // Re-frame the camera as objects stream in (until the user interacts).
  maybeAutoFrame();
  log(statusLine());
}

function statusLine() {
  return `<b>vid2sim viewer</b>\n` +
    `objects: ${loadedIds.size}\n` +
    `<span class="key">click</span> select · ` +
    `<span class="key">drag</span> push · ` +
    `<span class="key">space</span> drop ball · ` +
    `<span class="key">f</span> frame all\n` +
    `(sparse scene: only well-observed "tsdf" objects exist — generative ones\n` +
    ` are deferred until a GPU is connected)`;
}

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------
async function boot() {
  log("initialising physics…");
  await RAPIER.init();

  const sceneJson = await getScene();

  // Camera from capture pose when present (fix W5); the controls target is then
  // aimed at the bbox of ALL objects (maybeAutoFrame) so the start view frames
  // the whole room. Without a camera_pose we auto-frame fully (frameAll).
  const cp = sceneJson.camera_pose;
  hasCameraPose = !!(cp && cp.translation);
  if (hasCameraPose) {
    camera.position.set(cp.translation[0], cp.translation[1], cp.translation[2]);
  }

  // Gravity FROM scene.json (fix Z-J), not hardcoded. Assert the up axis.
  const g = (sceneJson.world && sceneJson.world.gravity) || [0, -9.81, 0];
  if (sceneJson.world && sceneJson.world.up_axis && sceneJson.world.up_axis !== "y") {
    console.warn("scene up_axis is not 'y'; renderer assumes Y-up");
  }
  world = new RAPIER.World({ x: g[0], y: g[1], z: g[2] });
  world.timestep = 1 / 60; // fixed dt (plan §16 step 5)

  // Ground from scene.json ground.y (fix K4). cuboid() takes HALF-extents (Z6),
  // so cuboid(50,0.1,50) is 100 x 0.2 x 100 m; centre it so the TOP face sits at
  // ground.y -> centre.y = ground.y - 0.1.
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
  const groundMesh = new THREE.Mesh(
    new THREE.PlaneGeometry(100, 100),
    new THREE.MeshStandardMaterial({ color: 0x3a3f47, roughness: 0.95, metalness: 0.0 })
  );
  groundMesh.rotation.x = -Math.PI / 2;
  groundMesh.position.y = gy;
  groundMesh.receiveShadow = true;
  scene.add(groundMesh);
  scene.add(new THREE.GridHelper(100, 100, 0x2a2e35, 0x23262c));
  scene.getObjectByProperty("type", "GridHelper").position.y = gy + 0.001;

  // Any objects already in scene.json at startup get added immediately; the SSE
  // stream then (re-)announces them and any that arrive later.
  for (const o of sceneJson.objects || []) await addObject(o.id, sceneJson);

  // SSE: on each object_added, re-GET scene.json and look up by id (fix Z-C).
  const es = new EventSource("/events");
  es.addEventListener("object_added", async (ev) => {
    const id = JSON.parse(ev.data).id;
    const fresh = await getScene();
    await addObject(id, fresh);
  });
  es.onerror = () => { /* server closed / reconnecting — harmless for a replay */ };

  log(statusLine());
  setupInteraction();
  animate();
}

// ---------------------------------------------------------------------------
// Interaction: click-select, drag-push, spacebar ball
// ---------------------------------------------------------------------------
const raycaster = new THREE.Raycaster();
const ndc = new THREE.Vector2();
let selected = null;        // { body, mesh, savedEmissive }
let dragging = false;
const dragPlane = new THREE.Plane();
const dragPoint = new THREE.Vector3();

function bodyFor(object3d) {
  for (const [b, m] of syncMap) if (m === object3d) return b;
  return null;
}

function rootOf(hitObject) {
  let o = hitObject;
  while (o && !meshToEntry.has(o)) o = o.parent;
  return o;
}

function setNdc(e) {
  ndc.x = (e.clientX / window.innerWidth) * 2 - 1;
  ndc.y = -(e.clientY / window.innerHeight) * 2 + 1;
}

function highlight(object3d, on) {
  object3d.traverse((o) => {
    if (o.isMesh && o.material) {
      if (on) {
        o.material = o.material.clone();
        o.material.emissive = new THREE.Color(0xff7a18);
        o.material.emissiveIntensity = 0.6;
      } else if (o.material.emissive) {
        o.material.emissive = new THREE.Color(0x000000);
      }
    }
  });
}

function setupInteraction() {
  const dom = renderer.domElement;

  // Any camera interaction (orbit/zoom/pan start, or a click on the canvas)
  // stops the streaming auto-frame from fighting the user.
  controls.addEventListener("start", () => { userInteracted = true; });
  dom.addEventListener("pointerdown", () => { userInteracted = true; });

  dom.addEventListener("pointerdown", (e) => {
    setNdc(e);
    raycaster.setFromCamera(ndc, camera);
    const hits = raycaster.intersectObjects(bodyMeshes, true);
    if (selected) { highlight(selected.mesh, false); selected = null; }
    if (hits.length) {
      const root = rootOf(hits[0].object);
      const body = root && bodyFor(root);
      if (body) {
        // wake the object into physics on first touch: fixed -> dynamic so it can
        // be pushed / fall. Only the clicked object moves, so no chain explosion.
        if (body.bodyType() !== RAPIER.RigidBodyType.Dynamic) {
          body.setBodyType(RAPIER.RigidBodyType.Dynamic, true);
        }
        selected = { body, mesh: root };
        highlight(root, true);
        // set up a drag plane through the hit point, facing the camera
        dragPlane.setFromNormalAndCoplanarPoint(
          camera.getWorldDirection(new THREE.Vector3()).negate(),
          hits[0].point
        );
        dragging = true;
        controls.enabled = false;
      }
    }
  });

  dom.addEventListener("pointermove", (e) => {
    if (!dragging || !selected) return;
    setNdc(e);
    raycaster.setFromCamera(ndc, camera);
    if (raycaster.ray.intersectPlane(dragPlane, dragPoint)) {
      // velocity toward the cursor (a soft impulse, not a teleport)
      const p = selected.body.translation();
      const v = {
        x: (dragPoint.x - p.x) * 6,
        y: (dragPoint.y - p.y) * 6,
        z: (dragPoint.z - p.z) * 6,
      };
      selected.body.setLinvel(v, true);
      selected.body.wakeUp();
    }
  });

  const endDrag = () => { dragging = false; controls.enabled = true; };
  dom.addEventListener("pointerup", endDrag);
  dom.addEventListener("pointerleave", endDrag);

  window.addEventListener("keydown", (e) => {
    if (e.code === "Space") { e.preventDefault(); spawnBall(); }
    if (e.code === "KeyF") frameAll(); // re-frame ALL objects on demand
  });
}

function spawnBall() {
  if (!world) return;
  const dir = camera.getWorldDirection(new THREE.Vector3());
  const pos = camera.position.clone().add(dir.multiplyScalar(2));
  const r = 0.1;

  const body = world.createRigidBody(
    RAPIER.RigidBodyDesc.dynamic().setTranslation(pos.x, pos.y, pos.z)
      .setAdditionalMass(0.2)
  );
  world.createCollider(
    RAPIER.ColliderDesc.ball(r).setDensity(0).setRestitution(0.7).setFriction(0.5),
    body
  );
  const mesh = new THREE.Mesh(
    new THREE.SphereGeometry(r, 24, 16),
    new THREE.MeshStandardMaterial({ color: 0xff5a3c, roughness: 0.5 })
  );
  mesh.castShadow = true;
  scene.add(mesh);
  syncMap.set(body, mesh);
  tempBalls.push({ body, mesh, dieAt: performance.now() + 10000 });
}

// ---------------------------------------------------------------------------
// Render loop (60 FPS)
// ---------------------------------------------------------------------------
function animate() {
  requestAnimationFrame(animate);
  if (world) {
    world.step();
    for (const [body, mesh] of syncMap) {
      const p = body.translation();
      const r = body.rotation();
      mesh.position.set(p.x, p.y, p.z);
      mesh.quaternion.set(r.x, r.y, r.z, r.w);
    }
    // expire spawned balls (10 s TTL)
    const now = performance.now();
    for (let i = tempBalls.length - 1; i >= 0; i--) {
      if (now >= tempBalls[i].dieAt) {
        const { body, mesh } = tempBalls[i];
        syncMap.delete(body);
        scene.remove(mesh);
        world.removeRigidBody(body);
        tempBalls.splice(i, 1);
      }
    }
  }
  controls.update();
  renderer.render(scene, camera);
}

boot().catch((err) => { console.error(err); log("error: " + err.message); });
