// SobaViewer — the framework-free viewer core (Phase 1 extraction of app.js).
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
//
// Boundary rule: this class NEVER touches DOM outside the canvas it is given
// (window/canvas event listeners and offscreen label canvases are fine; no
// selectors, no overlay elements). UI concerns live with the caller and are
// driven by events:
//   ready              {objects}            boot finished, rAF running
//   object-loaded      {id, count}          an object entered scene + physics
//   selection-changed  {id | null}          click select / deselect
//   stats              {fps, objects, dynamicBodies}   throttled to ~4 Hz
//   error              Error                boot failed
//
// Usage:  const v = new SobaViewer();  v.on("ready", ...);  await v.mount(canvas);

import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import RAPIER from "@dimforge/rapier3d-compat";

const STATS_INTERVAL_MS = 250; // ~4 Hz

export class SobaViewer {
  constructor() {
    this._handlers = Object.create(null);

    // Three.js / Rapier state (built in mount)
    this.renderer = null;
    this.scene = null;
    this.camera = null;
    this.controls = null;
    this.world = null;                 // RAPIER.World
    this.syncMap = new Map();          // rigidBody -> THREE.Object3D
    this.bodyMeshes = [];              // selectable THREE meshes (for raycasting)
    this.meshToEntry = new Map();      // THREE.Object3D -> scene.json object entry
    this.loadedIds = new Set();        // ids already added
    this.tempBalls = [];               // {body, mesh, dieAt}
    this.hasCameraPose = false;        // scene.json camera_pose wins initial placement (W5)
    this.userInteracted = false;       // stop auto-framing once the user touches the camera

    // interaction state
    this._raycaster = new THREE.Raycaster();
    this._ndc = new THREE.Vector2();
    this._selected = null;             // { body, mesh }
    this._dragging = false;
    this._dragPlane = new THREE.Plane();
    this._dragPoint = new THREE.Vector3();

    this._gltfLoader = new GLTFLoader();
    this._es = null;                   // EventSource
    this._raf = 0;
    this._disposed = false;
    this._boundListeners = [];         // [target, type, fn] for dispose()
    this._statsLastT = 0;
    this._statsFrames = 0;

    // Debug handle for headless verification (scripts/verify_browser.js).
    // Additive and harmless: getters read live Rapier state, nothing in the
    // viewer uses it. Shape is FROZEN (frontend/CLAUDE.md):
    // {objects: [{id, massKg, bodyType, position}], framedAll, screenPos(id)}
    this._dbg = { objects: [], framedAll: false, screenPos: null };
  }

  // ---- events -------------------------------------------------------------
  on(event, cb) {
    (this._handlers[event] ??= new Set()).add(cb);
    return () => this._handlers[event].delete(cb);
  }

  _emit(event, payload) {
    for (const cb of this._handlers[event] ?? []) {
      try { cb(payload); } catch (e) { console.error("SobaViewer handler error", e); }
    }
  }

  _listen(target, type, fn, opts) {
    target.addEventListener(type, fn, opts);
    this._boundListeners.push([target, type, fn]);
  }

  // ---- lifecycle ----------------------------------------------------------
  async mount(canvas) {
    window.__soba = this._dbg;

    const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(window.innerWidth, window.innerHeight);
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.0;
    this.renderer = renderer;

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x15171c);
    this.scene = scene;

    const camera = new THREE.PerspectiveCamera(
      55, window.innerWidth / window.innerHeight, 0.05, 200
    );
    camera.position.set(3, 2.4, 3); // default; overridden by camera_pose below
    this.camera = camera;

    this.controls = new OrbitControls(camera, renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.08;

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

    this._listen(window, "resize", () => {
      camera.aspect = window.innerWidth / window.innerHeight;
      camera.updateProjectionMatrix();
      renderer.setSize(window.innerWidth, window.innerHeight);
    });

    try {
      await this._boot();
    } catch (err) {
      console.error(err);
      this._emit("error", err);
      throw err;
    }
    return this;
  }

  dispose() {
    this._disposed = true;
    cancelAnimationFrame(this._raf);
    if (this._es) { this._es.close(); this._es = null; }
    for (const [target, type, fn] of this._boundListeners) {
      target.removeEventListener(type, fn);
    }
    this._boundListeners.length = 0;
    this.controls?.dispose();
    this.renderer?.dispose();
    if (this.world) { this.world.free(); this.world = null; }
    if (window.__soba === this._dbg) delete window.__soba;
  }

  // ---- helpers ------------------------------------------------------------
  async _getScene() {
    const r = await fetch("/scene.json", { cache: "no-store" });
    return r.json();
  }

  // Pull a flat Float32Array of world-space vertices out of a loaded GLB (bakes
  // any node transform in, so Rapier's convex hull matches what is rendered).
  static _glbVertices(gltf) {
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

  static _enableShadows(obj) {
    obj.traverse((o) => {
      if (o.isMesh) { o.castShadow = true; o.receiveShadow = true; }
    });
  }

  // ---- camera framing (DEBT A): frame the WHOLE ROOM, not one object ------
  _sceneBounds() {
    if (!this.bodyMeshes.length) return null;
    this.scene.updateMatrixWorld(true); // objects may not have rendered yet
    const box = new THREE.Box3();
    for (const m of this.bodyMeshes) box.expandByObject(m);
    return box.isEmpty() ? null : box;
  }

  // Fit the bbox of ALL loaded meshes into the view: OrbitControls target at
  // the bbox centre, camera along a pleasant 35°-elevation diagonal, distance
  // chosen so the bounding sphere fits the narrower FOV axis with ~15% margin.
  frameAll() {
    const box = this._sceneBounds();
    if (!box) return;
    const center = box.getCenter(new THREE.Vector3());
    const sphere = box.getBoundingSphere(new THREE.Sphere());
    const vFov = THREE.MathUtils.degToRad(this.camera.fov);
    const hFov = 2 * Math.atan(Math.tan(vFov / 2) * this.camera.aspect);
    const fov = Math.min(vFov, hFov);
    const dist = Math.max(0.5, (sphere.radius * 1.15) / Math.sin(fov / 2));
    const elev = THREE.MathUtils.degToRad(35);
    const azim = THREE.MathUtils.degToRad(45);
    const dir = new THREE.Vector3(
      Math.cos(elev) * Math.sin(azim),
      Math.sin(elev),
      Math.cos(elev) * Math.cos(azim)
    );
    this.camera.position.copy(center).addScaledVector(dir, dist);
    this.controls.target.copy(center);
    this.controls.update();
    this._dbg.framedAll = true;
  }

  // Called after each object loads. Auto-frame only until the user first
  // touches the camera (don't fight the user). When scene.json has a
  // camera_pose it wins the INITIAL camera position (fix W5) — then we only
  // aim the controls target at the whole scene; "f" re-frames fully any time.
  _maybeAutoFrame() {
    if (this.userInteracted) return;
    if (this.hasCameraPose) {
      const box = this._sceneBounds();
      if (box) {
        this.controls.target.copy(box.getCenter(new THREE.Vector3()));
        this.controls.update();
      }
    } else {
      this.frameAll();
    }
  }

  // Floating text label (canvas-texture sprite) that always faces the camera.
  // The canvas is offscreen texture backing, never appended to the document.
  static _makeLabel(text, hex) {
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

  // ---- object loading (one object_added event) ----------------------------
  async _addObject(id, sceneJson) {
    if (this.loadedIds.has(id)) return;
    const entry = (sceneJson.objects || []).find((o) => o.id === id);
    if (!entry) { console.warn("no scene.json entry for", id); return; }
    this.loadedIds.add(id);

    const t = entry.transform;
    const [tx, ty, tz] = t.translation;
    const q = t.rotation_quat; // [x,y,z,w]

    // 1. Render mesh (do NOT collapse to one mesh — that strips PBR materials).
    const gltf = await this._gltfLoader.loadAsync(`/meshes/${id}.glb`);
    const obj3d = gltf.scene;
    // Render DOUBLE-SIDED: these are TSDF / marching-cubes / completion meshes
    // whose triangle winding isn't perfectly consistent (e.g. Taubin smoothing
    // can flip normals on thin features), so single-sided (default) culls the
    // back-facing ones and they read as SEE-THROUGH HOLES. DoubleSide renders
    // both faces -> the surface looks solid regardless of winding. Also apply
    // the optional per-object comparison color.
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
    SobaViewer._enableShadows(obj3d);
    obj3d.position.set(tx, ty, tz);
    obj3d.quaternion.set(q[0], q[1], q[2], q[3]);
    this.scene.add(obj3d);
    this.bodyMeshes.push(obj3d);
    this.meshToEntry.set(obj3d, entry);

    // floating label above the object (model · object), color-underlined
    if (entry.label) {
      const hy = entry.collider?.half_extents?.[1] ?? 0.5;
      const label = SobaViewer._makeLabel(entry.label, entry.color ?? 0xffffff);
      label.position.set(tx, ty + hy + 0.45, tz);
      this.scene.add(label);
    }

    // 5. Rigid body. Objects start FIXED (static) so a reconstructed room
    // LOADS STABLE — the meshes are placed at their observed positions and are
    // often sized by a class prior, so several can overlap; if they were all
    // dynamic on load, Rapier ejects the interpenetrations and the whole scene
    // EXPLODES. Each body becomes dynamic on demand when you click it (see
    // _setupInteraction), so you can still push it / drop the ball on it.
    // Mass is set on the desc (fix P1/D5) and applies once the body turns
    // dynamic.
    const phys = entry.physics;
    const desc = RAPIER.RigidBodyDesc.fixed()
      .setTranslation(tx, ty, tz)
      .setRotation({ x: q[0], y: q[1], z: q[2], w: q[3] })
      .setAdditionalMass(phys.mass_kg);
    const body = this.world.createRigidBody(desc);

    // 6. Colliders. Tiers 2-4: CoACD hulls (density 0). Tier 1: AABB box.
    const col = entry.collider;
    if (col.shape === "box") {
      const [hx, hy, hz] = col.half_extents;
      const cdesc = RAPIER.ColliderDesc.cuboid(hx, hy, hz)
        .setDensity(0)
        .setFriction(phys.friction)
        .setRestitution(phys.restitution);
      this.world.createCollider(cdesc, body);
    } else {
      // hull_paths are scene-relative ("hulls/{id}_{i}.glb"); the server
      // remaps /hulls/{id}_{i}.glb -> objects/{id}/hulls/{id}_{i}.glb.
      const hullGltfs = await Promise.all(
        col.hull_paths.map((p) => this._gltfLoader.loadAsync("/" + p))
      );
      for (const hg of hullGltfs) {
        const verts = SobaViewer._glbVertices(hg);
        const cdesc = RAPIER.ColliderDesc.convexHull(verts);
        if (!cdesc) { console.warn("degenerate hull for", id); continue; }
        cdesc.setDensity(0).setFriction(phys.friction).setRestitution(phys.restitution);
        this.world.createCollider(cdesc, body);
      }
    }

    this.syncMap.set(body, obj3d);

    // Verification handle entry: getters read LIVE Rapier state so a headless
    // checker sees exactly what the physics world holds (mass regression guard).
    this._dbg.objects.push({
      id,
      get massKg() { return body.mass(); },
      get bodyType() {
        const t = body.bodyType();
        return t === RAPIER.RigidBodyType.Dynamic ? "dynamic"
          : t === RAPIER.RigidBodyType.Fixed ? "fixed" : "kinematic";
      },
      get position() { const p = body.translation(); return [p.x, p.y, p.z]; },
    });

    // Re-frame the camera as objects stream in (until the user interacts).
    this._maybeAutoFrame();
    this._emit("object-loaded", { id, count: this.loadedIds.size });
  }

  // ---- boot ---------------------------------------------------------------
  async _boot() {
    await RAPIER.init();

    const sceneJson = await this._getScene();

    // Camera from capture pose when present (fix W5); the controls target is
    // then aimed at the bbox of ALL objects (_maybeAutoFrame) so the start
    // view frames the whole room. Without a camera_pose we auto-frame fully.
    const cp = sceneJson.camera_pose;
    this.hasCameraPose = !!(cp && cp.translation);
    if (this.hasCameraPose) {
      this.camera.position.set(cp.translation[0], cp.translation[1], cp.translation[2]);
    }

    // Gravity FROM scene.json (fix Z-J), not hardcoded. Assert the up axis.
    const g = (sceneJson.world && sceneJson.world.gravity) || [0, -9.81, 0];
    if (sceneJson.world && sceneJson.world.up_axis && sceneJson.world.up_axis !== "y") {
      console.warn("scene up_axis is not 'y'; renderer assumes Y-up");
    }
    this.world = new RAPIER.World({ x: g[0], y: g[1], z: g[2] });
    this.world.timestep = 1 / 60; // fixed dt (plan §16 step 5)

    // Ground from scene.json ground.y (fix K4). cuboid() takes HALF-extents
    // (Z6), so cuboid(50,0.1,50) is 100 x 0.2 x 100 m; centre it so the TOP
    // face sits at ground.y -> centre.y = ground.y - 0.1.
    const ground = sceneJson.ground || { y: 0, material: {} };
    const gy = ground.y || 0;
    const gmat = ground.material || { friction: 0.85, restitution: 0.1 };
    const groundBody = this.world.createRigidBody(
      RAPIER.RigidBodyDesc.fixed().setTranslation(0, gy - 0.1, 0)
    );
    this.world.createCollider(
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
    this.scene.add(groundMesh);
    const grid = new THREE.GridHelper(100, 100, 0x2a2e35, 0x23262c);
    grid.position.y = gy + 0.001;
    this.scene.add(grid);

    // Any objects already in scene.json at startup get added immediately; the
    // SSE stream then (re-)announces them and any that arrive later.
    for (const o of sceneJson.objects || []) await this._addObject(o.id, sceneJson);

    // SSE: on each object_added, re-GET scene.json and look up by id (fix Z-C).
    this._es = new EventSource("/events");
    this._es.addEventListener("object_added", async (ev) => {
      const id = JSON.parse(ev.data).id;
      const fresh = await this._getScene();
      await this._addObject(id, fresh);
    });
    this._es.onerror = () => { /* server closed / reconnecting — harmless for a replay */ };

    this._setupInteraction();
    this._animate();
    this._emit("ready", { objects: this.loadedIds.size });
  }

  // ---- interaction: click-select, drag-push, spacebar ball ----------------
  _bodyFor(object3d) {
    for (const [b, m] of this.syncMap) if (m === object3d) return b;
    return null;
  }

  _rootOf(hitObject) {
    let o = hitObject;
    while (o && !this.meshToEntry.has(o)) o = o.parent;
    return o;
  }

  _setNdc(e) {
    this._ndc.x = (e.clientX / window.innerWidth) * 2 - 1;
    this._ndc.y = -(e.clientY / window.innerHeight) * 2 + 1;
  }

  static _highlight(object3d, on) {
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

  _setupInteraction() {
    const dom = this.renderer.domElement;

    // Any camera interaction (orbit/zoom/pan start, or a click on the canvas)
    // stops the streaming auto-frame from fighting the user.
    this.controls.addEventListener("start", () => { this.userInteracted = true; });

    // Screen-space centre of an object (pixels) — lets the headless verifier
    // click objects through the REAL pointer path instead of poking Rapier.
    this._dbg.screenPos = (id) => {
      for (const [mesh, entry] of this.meshToEntry) {
        if (entry.id !== id) continue;
        const p = mesh.position.clone().project(this.camera);
        return [(p.x + 1) / 2 * window.innerWidth, (1 - p.y) / 2 * window.innerHeight];
      }
      return null;
    };

    this._listen(dom, "pointerdown", (e) => {
      this.userInteracted = true;
      this._setNdc(e);
      this._raycaster.setFromCamera(this._ndc, this.camera);
      const hits = this._raycaster.intersectObjects(this.bodyMeshes, true);
      if (this._selected) {
        SobaViewer._highlight(this._selected.mesh, false);
        this._selected = null;
        this._emit("selection-changed", { id: null });
      }
      if (hits.length) {
        const root = this._rootOf(hits[0].object);
        const body = root && this._bodyFor(root);
        if (body) {
          // wake the object into physics on first touch: fixed -> dynamic so
          // it can be pushed / fall. Only the clicked object moves, so no
          // chain explosion.
          if (body.bodyType() !== RAPIER.RigidBodyType.Dynamic) {
            body.setBodyType(RAPIER.RigidBodyType.Dynamic, true);
          }
          this._selected = { body, mesh: root };
          SobaViewer._highlight(root, true);
          this._emit("selection-changed", { id: this.meshToEntry.get(root)?.id ?? null });
          // set up a drag plane through the hit point, facing the camera
          this._dragPlane.setFromNormalAndCoplanarPoint(
            this.camera.getWorldDirection(new THREE.Vector3()).negate(),
            hits[0].point
          );
          this._dragging = true;
          this.controls.enabled = false;
        }
      }
    });

    this._listen(dom, "pointermove", (e) => {
      if (!this._dragging || !this._selected) return;
      this._setNdc(e);
      this._raycaster.setFromCamera(this._ndc, this.camera);
      if (this._raycaster.ray.intersectPlane(this._dragPlane, this._dragPoint)) {
        // velocity toward the cursor (a soft impulse, not a teleport)
        const p = this._selected.body.translation();
        const v = {
          x: (this._dragPoint.x - p.x) * 6,
          y: (this._dragPoint.y - p.y) * 6,
          z: (this._dragPoint.z - p.z) * 6,
        };
        this._selected.body.setLinvel(v, true);
        this._selected.body.wakeUp();
      }
    });

    const endDrag = () => { this._dragging = false; this.controls.enabled = true; };
    this._listen(dom, "pointerup", endDrag);
    this._listen(dom, "pointerleave", endDrag);

    this._listen(window, "keydown", (e) => {
      if (e.code === "Space") { e.preventDefault(); this.spawnBall(); }
      if (e.code === "KeyF") this.frameAll(); // re-frame ALL objects on demand
    });
  }

  spawnBall() {
    if (!this.world) return;
    const dir = this.camera.getWorldDirection(new THREE.Vector3());
    const pos = this.camera.position.clone().add(dir.multiplyScalar(2));
    const r = 0.1;

    const body = this.world.createRigidBody(
      RAPIER.RigidBodyDesc.dynamic().setTranslation(pos.x, pos.y, pos.z)
        .setAdditionalMass(0.2)
    );
    this.world.createCollider(
      RAPIER.ColliderDesc.ball(r).setDensity(0).setRestitution(0.7).setFriction(0.5),
      body
    );
    const mesh = new THREE.Mesh(
      new THREE.SphereGeometry(r, 24, 16),
      new THREE.MeshStandardMaterial({ color: 0xff5a3c, roughness: 0.5 })
    );
    mesh.castShadow = true;
    this.scene.add(mesh);
    this.syncMap.set(body, mesh);
    this.tempBalls.push({ body, mesh, dieAt: performance.now() + 10000 });
  }

  // ---- render loop (60 FPS) -----------------------------------------------
  _animate() {
    if (this._disposed) return;
    this._raf = requestAnimationFrame(() => this._animate());
    if (this.world) {
      this.world.step();
      for (const [body, mesh] of this.syncMap) {
        const p = body.translation();
        const r = body.rotation();
        mesh.position.set(p.x, p.y, p.z);
        mesh.quaternion.set(r.x, r.y, r.z, r.w);
      }
      // expire spawned balls (10 s TTL)
      const now = performance.now();
      for (let i = this.tempBalls.length - 1; i >= 0; i--) {
        if (now >= this.tempBalls[i].dieAt) {
          const { body, mesh } = this.tempBalls[i];
          this.syncMap.delete(body);
          this.scene.remove(mesh);
          this.world.removeRigidBody(body);
          this.tempBalls.splice(i, 1);
        }
      }
    }
    this.controls.update();
    this.renderer.render(this.scene, this.camera);

    // throttled stats event (~4 Hz) — discrete, cheap, safe for UI state
    this._statsFrames++;
    const now = performance.now();
    if (now - this._statsLastT >= STATS_INTERVAL_MS) {
      if (this._statsLastT > 0) {
        const fps = Math.round((this._statsFrames * 1000) / (now - this._statsLastT));
        let dynamicBodies = 0;
        for (const body of this.syncMap.keys()) {
          if (body.bodyType() === RAPIER.RigidBodyType.Dynamic) dynamicBodies++;
        }
        this._emit("stats", { fps, objects: this.loadedIds.size, dynamicBodies });
      }
      this._statsLastT = now;
      this._statsFrames = 0;
    }
  }
}
