// SobaViewer — the framework-free viewer core. Three.js renders the assembled
// scene; Rapier (WASM) simulates it. Construction correctness lives in
// physics.ts; framing math in framing.ts; mesh helpers in loaders.ts.
//
// Remaining load-bearing behaviour owned here (plan §16, frontend/CLAUDE.md):
//   * each object_added SSE event triggers a re-GET of /scene.json and a lookup
//     by id (fix Z-C); the event carries only the id.
//   * the camera starts from scene.json camera_pose when present (fix W5); the
//     view then frames the bbox of ALL objects (auto until the user interacts,
//     "f" to re-frame any time) so a full room never looks like one lone object.
//
// Boundary rule: this class NEVER touches DOM outside the canvas it is given
// (window/canvas event listeners and offscreen label canvases are fine; no
// selectors, no overlay elements). UI concerns live with the caller and are
// driven by events — see ViewerEventMap in types.ts.
//
// Usage:  const v = new SobaViewer();  v.on("ready", ...);  await v.mount(canvas);

import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import RAPIER, { type RigidBody } from "@dimforge/rapier3d-compat";
import type { World } from "@dimforge/rapier3d-compat";
import type { ObjectInfo, SceneJson, SceneObject, SobaDebug, ViewerEventMap } from "./types";
import { glbVertices, enableShadows, makeLabel, sceneUrl } from "./loaders";
import { boundsOf, framePlacement } from "./framing";
import { initWorld, createGroundCollider, createObjectBody, attachColliders } from "./physics";

const STATS_INTERVAL_MS = 250; // ~4 Hz

type Handler = (payload: never) => void;

// base colour of a selected object while tints are on (the accent token)
const SELECTION_TINT = 0xffb454;

export class SobaViewer {
  private renderer: THREE.WebGLRenderer | null = null;
  private scene: THREE.Scene | null = null;
  private camera: THREE.PerspectiveCamera | null = null;
  private controls: OrbitControls | null = null;
  private world: World | null = null;

  private syncMap = new Map<RigidBody, THREE.Object3D>();   // rigidBody -> mesh
  private bodyMeshes: THREE.Object3D[] = [];                // selectable roots
  private meshToEntry = new Map<THREE.Object3D, SceneObject>();
  private loadedIds = new Set<string>();
  private tempBalls: { body: RigidBody; mesh: THREE.Mesh; dieAt: number }[] = [];
  private hasCameraPose = false;   // scene.json camera_pose wins initial placement (W5)
  private userInteracted = false;  // stop auto-framing once the user touches the camera
  // eased camera move (presentation tour). Camera motion is content, not UI
  // chrome: it runs inside the rAF loop and never enters React state.
  private camMove: {
    fromPos: THREE.Vector3; toPos: THREE.Vector3;
    fromTarget: THREE.Vector3; toTarget: THREE.Vector3;
    t0: number; ms: number;
  } | null = null;

  private raycaster = new THREE.Raycaster();
  private ndc = new THREE.Vector2();
  private selected: { body: RigidBody; mesh: THREE.Object3D } | null = null;
  private dragging = false;
  private dragPlane = new THREE.Plane();
  private dragPoint = new THREE.Vector3();

  private gltfLoader = new GLTFLoader();
  private es: EventSource | null = null;
  private raf = 0;
  private disposed = false;
  private boundListeners: [EventTarget, string, EventListener][] = [];
  private statsLastT = 0;
  private statsFrames = 0;

  private handlers: Partial<Record<keyof ViewerEventMap, Set<Handler>>> = {};

  // Debug handle for headless verification (scripts/verify_browser.js).
  // Additive and harmless: getters read live Rapier state, nothing in the
  // viewer uses it. Shape is FROZEN (frontend/CLAUDE.md):
  // {objects: [{id, massKg, bodyType, position}], framedAll, screenPos(id)}
  private dbg: SobaDebug = { objects: [], framedAll: false, screenPos: null };

  // ---- events -------------------------------------------------------------
  on<K extends keyof ViewerEventMap>(
    event: K, cb: (payload: ViewerEventMap[K]) => void
  ): () => void {
    (this.handlers[event] ??= new Set()).add(cb as Handler);
    return () => this.handlers[event]?.delete(cb as Handler);
  }

  private emit<K extends keyof ViewerEventMap>(event: K, payload: ViewerEventMap[K]): void {
    for (const cb of this.handlers[event] ?? []) {
      try { (cb as (p: ViewerEventMap[K]) => void)(payload); }
      catch (e) { console.error("SobaViewer handler error", e); }
    }
  }

  private listen(target: EventTarget, type: string, fn: EventListener): void {
    target.addEventListener(type, fn);
    this.boundListeners.push([target, type, fn]);
  }

  // ---- lifecycle ----------------------------------------------------------
  async mount(canvas: HTMLCanvasElement): Promise<this> {
    window.__soba = this.dbg;

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

    this.listen(window, "resize", () => {
      camera.aspect = window.innerWidth / window.innerHeight;
      camera.updateProjectionMatrix();
      renderer.setSize(window.innerWidth, window.innerHeight);
    });

    try {
      await this.boot();
    } catch (err) {
      console.error(err);
      this.emit("error", err instanceof Error ? err : new Error(String(err)));
      throw err;
    }
    return this;
  }

  dispose(): void {
    this.disposed = true;
    cancelAnimationFrame(this.raf);
    if (this.es) { this.es.close(); this.es = null; }
    for (const [target, type, fn] of this.boundListeners) {
      target.removeEventListener(type, fn);
    }
    this.boundListeners.length = 0;
    this.controls?.dispose();
    this.renderer?.dispose();
    if (this.world) { this.world.free(); this.world = null; }
    if (window.__soba === this.dbg) delete window.__soba;
  }

  // ---- helpers ------------------------------------------------------------
  private async getScene(): Promise<SceneJson> {
    const r = await fetch(sceneUrl("/scene.json"), { cache: "no-store" });
    return r.json();
  }

  // ---- camera framing -----------------------------------------------------
  frameAll(): void {
    if (!this.scene || !this.camera || !this.controls) return;
    const box = boundsOf(this.scene, this.bodyMeshes);
    if (!box) return;
    const { center, position } = framePlacement(box, this.camera.fov, this.camera.aspect);
    this.camera.position.copy(position);
    this.controls.target.copy(center);
    this.controls.update();
    this.dbg.framedAll = true;
  }

  // Called after each object loads. Auto-frame only until the user first
  // touches the camera (don't fight the user). When scene.json has a
  // camera_pose it wins the INITIAL camera position (fix W5) — then we only
  // aim the controls target at the whole scene; "f" re-frames fully any time.
  private maybeAutoFrame(): void {
    if (this.userInteracted || !this.scene || !this.controls) return;
    if (this.hasCameraPose) {
      const box = boundsOf(this.scene, this.bodyMeshes);
      if (box) {
        this.controls.target.copy(box.getCenter(new THREE.Vector3()));
        this.controls.update();
      }
    } else {
      this.frameAll();
    }
  }

  // ---- object loading (one object_added event) ----------------------------
  private async addObject(id: string, sceneJson: SceneJson): Promise<void> {
    if (this.loadedIds.has(id) || this.disposed) return;
    const entry = (sceneJson.objects || []).find((o) => o.id === id);
    if (!entry) { console.warn("no scene.json entry for", id); return; }
    this.loadedIds.add(id);

    const [tx, ty, tz] = entry.transform.translation;
    const q = entry.transform.rotation_quat; // [x,y,z,w]

    // 1. Render mesh (do NOT collapse to one mesh — that strips PBR materials).
    const gltf = await this.gltfLoader.loadAsync(sceneUrl(`/meshes/${id}.glb`));
    if (this.disposed) return;
    const obj3d = gltf.scene;
    // Render DOUBLE-SIDED: these are TSDF / marching-cubes / completion meshes
    // whose triangle winding isn't perfectly consistent (e.g. Taubin smoothing
    // can flip normals on thin features), so single-sided (default) culls the
    // back-facing ones and they read as SEE-THROUGH HOLES. DoubleSide renders
    // both faces -> the surface looks solid regardless of winding. Also apply
    // the optional per-object comparison color.
    const hasColor = entry.color !== undefined && entry.color !== null;
    obj3d.traverse((o) => {
      const mesh = o as THREE.Mesh;
      if (!mesh.isMesh) return;
      if (hasColor) {
        mesh.material = new THREE.MeshStandardMaterial({
          color: entry.color, roughness: 0.55, metalness: 0.0,
        });
      }
      if (mesh.material) (mesh.material as THREE.Material).side = THREE.DoubleSide;
    });
    enableShadows(obj3d);
    obj3d.position.set(tx, ty, tz);
    obj3d.quaternion.set(q[0], q[1], q[2], q[3]);
    this.scene!.add(obj3d);
    this.bodyMeshes.push(obj3d);
    this.meshToEntry.set(obj3d, entry);

    // floating label above the object (model · object), color-underlined
    if (entry.label) {
      const hy = entry.collider?.half_extents?.[1] ?? 0.5;
      const label = makeLabel(entry.label, entry.color ?? 0xffffff);
      label.position.set(tx, ty + hy + 0.45, tz);
      this.scene!.add(label);
    }

    // Rigid body + colliders (see physics.ts for the correctness contract).
    const body = createObjectBody(this.world!, entry);
    let hullVerts: Float32Array[] = [];
    if (entry.collider.shape !== "box") {
      // hull_paths are scene-relative ("hulls/{id}_{i}.glb"); the server
      // remaps /hulls/{id}_{i}.glb -> objects/{id}/hulls/{id}_{i}.glb.
      const hullGltfs = await Promise.all(
        (entry.collider.hull_paths ?? []).map((p) => this.gltfLoader.loadAsync(sceneUrl(p)))
      );
      hullVerts = hullGltfs.map(glbVertices);
    }
    attachColliders(this.world!, body, entry, hullVerts);

    this.syncMap.set(body, obj3d);

    // Verification handle entry: getters read LIVE Rapier state so a headless
    // checker sees exactly what the physics world holds (mass regression guard).
    this.dbg.objects.push({
      id,
      get massKg() { return body.mass(); },
      get bodyType() {
        const t = body.bodyType();
        return t === RAPIER.RigidBodyType.Dynamic ? "dynamic"
          : t === RAPIER.RigidBodyType.Fixed ? "fixed" : "kinematic";
      },
      get position(): [number, number, number] {
        const p = body.translation(); return [p.x, p.y, p.z];
      },
    });

    // Re-frame the camera as objects stream in (until the user interacts).
    this.maybeAutoFrame();
    this.emit("object-loaded", {
      id,
      count: this.loadedIds.size,
      info: SobaViewer.objectInfo(entry),
    });
  }

  // Plain-data projection of a scene.json entry for the UI — no live objects.
  private static objectInfo(entry: SceneObject): ObjectInfo {
    return {
      id: entry.id,
      cls: entry.class,
      massKg: entry.physics.mass_kg,
      material: entry.material_class,
      geometrySource: entry.source.geometry_source,
      alignmentMethod: entry.source.alignment_method,
      scaleMethod: entry.source.scale_method,
      physicsOrigin: entry.source.physics_origin,
      vlmReasoning: entry.source.vlm_reasoning ?? "",
      colliderShape: entry.collider.shape,
      colliderCount: entry.collider.shape === "box" ? 1 : (entry.collider.hull_paths?.length ?? 0),
      friction: entry.physics.friction,
      restitution: entry.physics.restitution,
    };
  }

  // Programmatic selection (inspector -> 3D). Highlight + selection state
  // ONLY: unlike a canvas click this must NOT wake the body into physics —
  // browsing the object list is not a physical interaction, and the frozen
  // click semantics (click = select + wake) stay untouched.
  selectById(id: string | null): void {
    if (this.selected) {
      SobaViewer.highlight(this.selected.mesh, false);
      this.selected = null;
    }
    if (id !== null) {
      for (const [mesh, entry] of this.meshToEntry) {
        if (entry.id !== id) continue;
        const body = this.bodyFor(mesh);
        if (!body) break;
        this.selected = { body, mesh };
        SobaViewer.highlight(mesh, true);
        this.emit("selection-changed", { id });
        return;
      }
    }
    this.emit("selection-changed", { id: null });
  }

  // ---- boot ---------------------------------------------------------------
  // ---- presentation helpers (store actions only; plain data in, none out) --

  /** Slow turntable around the current target. Any user camera input stops it. */
  setAutoOrbit(on: boolean): void {
    if (!this.controls) return;
    this.controls.autoRotate = on;
    this.controls.autoRotateSpeed = 0.6;
  }

  /** Ease the camera to frame one object (or the whole room for null),
   *  keeping the current viewing direction so the move reads as a dolly,
   *  not a cut. Does not select, wake or count as user interaction. */
  focusObject(id: string | null): void {
    if (!this.scene || !this.camera || !this.controls) return;
    let roots = this.bodyMeshes;
    if (id !== null) {
      roots = [];
      for (const [mesh, entry] of this.meshToEntry) if (entry.id === id) roots.push(mesh);
    }
    const box = boundsOf(this.scene, roots);
    if (!box) return;
    const { center, position } = framePlacement(box, this.camera.fov, this.camera.aspect);
    const dist = position.distanceTo(center) * (id === null ? 1 : 1.25); // a little context around one object
    const dir = this.camera.position.clone().sub(this.controls.target);
    if (dir.lengthSq() < 1e-6) dir.copy(position).sub(center);
    dir.normalize();
    const minY = Math.sin(THREE.MathUtils.degToRad(20)); // never graze the floor
    if (dir.y < minY) { dir.y = minY; dir.normalize(); }
    this.camMove = {
      fromPos: this.camera.position.clone(), toPos: center.clone().addScaledVector(dir, dist),
      fromTarget: this.controls.target.clone(), toTarget: center,
      t0: performance.now(), ms: 900,
    };
  }

  /** Tint objects by id (0xRRGGBB), or restore their own colours with null.
   *  Each object gets its own material once, so a tint never leaks to another
   *  object and the selection highlight (emissive) keeps working on top. */
  setTints(tints: Record<string, number> | null): void {
    for (const [root, entry] of this.meshToEntry) {
      const tint = tints ? tints[entry.id] : undefined;
      root.traverse((o) => {
        const mesh = o as THREE.Mesh;
        if (!mesh.isMesh || !mesh.material || Array.isArray(mesh.material)) return;
        let mat = mesh.material as THREE.MeshStandardMaterial;
        if (!mat.color) return;
        if (mesh.userData.sobaBaseColor === undefined) {
          mat = mat.clone();
          mesh.material = mat;
          mesh.userData.sobaBaseColor = mat.color.getHex();
          mesh.userData.sobaBaseMetalness = mat.metalness;
        }
        mesh.userData.sobaTinted = tint !== undefined;
        // the selected object wears the selection colour (see highlight());
        // it picks its tint up again on the setTints that follows deselection
        const selected = this.selected?.mesh === root && tint !== undefined;
        mat.color.setHex(selected ? SELECTION_TINT : tint ?? mesh.userData.sobaBaseColor);
        // Untextured pipeline meshes get GLTFLoader's default material
        // (metalness 1), which has no diffuse term: a tint would read as
        // near-black. Tinted = dielectric; untinted = exactly as loaded.
        mat.metalness = tint === undefined ? mesh.userData.sobaBaseMetalness : 0;
      });
    }
  }

  /** Put every object back where scene.json placed it: pose restored, at
   *  rest, FIXED again (the load-time stability contract). Balls are removed. */
  resetObjects(): void {
    if (!this.world || !this.scene) return;
    for (const [mesh, entry] of this.meshToEntry) {
      const body = this.bodyFor(mesh);
      if (!body) continue;
      const [x, y, z] = entry.transform.translation;
      const [qx, qy, qz, qw] = entry.transform.rotation_quat;
      body.setBodyType(RAPIER.RigidBodyType.Fixed, false);
      body.setLinvel({ x: 0, y: 0, z: 0 }, false);
      body.setAngvel({ x: 0, y: 0, z: 0 }, false);
      body.setTranslation({ x, y, z }, false);
      body.setRotation({ x: qx, y: qy, z: qz, w: qw }, false);
    }
    for (const { body, mesh } of this.tempBalls) {
      this.syncMap.delete(body);
      this.scene.remove(mesh);
      this.world.removeRigidBody(body);
    }
    this.tempBalls.length = 0;
  }

  private async boot(): Promise<void> {
    await RAPIER.init();
    if (this.disposed) return;

    const sceneJson = await this.getScene();
    if (this.disposed) return;

    // Camera from capture pose when present (fix W5); the controls target is
    // then aimed at the bbox of ALL objects (maybeAutoFrame) so the start
    // view frames the whole room. Without a camera_pose we auto-frame fully.
    const cp = sceneJson.camera_pose;
    this.hasCameraPose = !!(cp && cp.translation);
    if (this.hasCameraPose && cp) {
      this.camera!.position.set(cp.translation[0], cp.translation[1], cp.translation[2]);
    }

    this.world = initWorld(sceneJson);
    createGroundCollider(this.world, sceneJson);

    // Ground visuals (rendering only; the collider is physics.ts's job).
    const gy = sceneJson.ground?.y || 0;
    const groundMesh = new THREE.Mesh(
      new THREE.PlaneGeometry(100, 100),
      new THREE.MeshStandardMaterial({ color: 0x3a3f47, roughness: 0.95, metalness: 0.0 })
    );
    groundMesh.rotation.x = -Math.PI / 2;
    groundMesh.position.y = gy;
    groundMesh.receiveShadow = true;
    this.scene!.add(groundMesh);
    const grid = new THREE.GridHelper(100, 100, 0x2a2e35, 0x23262c);
    grid.position.y = gy + 0.001;
    this.scene!.add(grid);

    // Any objects already in scene.json at startup get added immediately; the
    // SSE stream then (re-)announces them and any that arrive later.
    for (const o of sceneJson.objects || []) await this.addObject(o.id, sceneJson);
    if (this.disposed) return;

    // SSE: on each object_added, re-GET scene.json and look up by id (fix Z-C).
    this.es = new EventSource(sceneUrl("/events"));
    this.es.addEventListener("object_added", async (ev) => {
      const id = JSON.parse((ev as MessageEvent).data).id;
      const fresh = await this.getScene();
      await this.addObject(id, fresh);
    });
    this.es.onerror = () => { /* server closed / reconnecting — harmless for a replay */ };

    this.setupInteraction();
    this.animate();
    this.emit("ready", { objects: this.loadedIds.size });
  }

  // ---- interaction: click-select, drag-push, spacebar ball ----------------
  private bodyFor(object3d: THREE.Object3D): RigidBody | null {
    for (const [b, m] of this.syncMap) if (m === object3d) return b;
    return null;
  }

  private rootOf(hitObject: THREE.Object3D): THREE.Object3D | null {
    let o: THREE.Object3D | null = hitObject;
    while (o && !this.meshToEntry.has(o)) o = o.parent;
    return o;
  }

  private setNdc(e: PointerEvent): void {
    this.ndc.x = (e.clientX / window.innerWidth) * 2 - 1;
    this.ndc.y = -(e.clientY / window.innerHeight) * 2 + 1;
  }

  private static highlight(object3d: THREE.Object3D, on: boolean): void {
    object3d.traverse((o) => {
      const mesh = o as THREE.Mesh;
      if (mesh.isMesh && mesh.material) {
        const mat = mesh.material as THREE.MeshStandardMaterial;
        if (on) {
          const clone = mat.clone();
          clone.emissive = new THREE.Color(0xff7a18);
          clone.emissiveIntensity = 0.6;
          // over a tint the emissive alone shifts hue (blue + orange reads
          // pink); selection must stay the accent colour
          if (mesh.userData.sobaTinted) clone.color.setHex(SELECTION_TINT);
          mesh.material = clone;
        } else if (mat.emissive) {
          mat.emissive = new THREE.Color(0x000000);
        }
      }
    });
  }

  private setupInteraction(): void {
    const dom = this.renderer!.domElement;
    const camera = this.camera!;
    const controls = this.controls!;

    // Any camera interaction (orbit/zoom/pan start, or a click on the canvas)
    // stops the streaming auto-frame from fighting the user.
    controls.addEventListener("start", () => {
      this.userInteracted = true;
      this.camMove = null;            // the user always wins over a tour move
      controls.autoRotate = false;
    });

    // Screen-space centre of an object (pixels) — lets the headless verifier
    // click objects through the REAL pointer path instead of poking Rapier.
    this.dbg.screenPos = (id: string) => {
      for (const [mesh, entry] of this.meshToEntry) {
        if (entry.id !== id) continue;
        const p = mesh.position.clone().project(camera);
        return [(p.x + 1) / 2 * window.innerWidth, (1 - p.y) / 2 * window.innerHeight];
      }
      return null;
    };

    this.listen(dom, "pointerdown", ((e: PointerEvent) => {
      this.userInteracted = true;
      this.setNdc(e);
      this.raycaster.setFromCamera(this.ndc, camera);
      const hits = this.raycaster.intersectObjects(this.bodyMeshes, true);
      if (this.selected) {
        SobaViewer.highlight(this.selected.mesh, false);
        this.selected = null;
        this.emit("selection-changed", { id: null });
      }
      if (hits.length) {
        const root = this.rootOf(hits[0].object);
        const body = root && this.bodyFor(root);
        if (root && body) {
          // wake the object into physics on first touch: fixed -> dynamic so
          // it can be pushed / fall. Only the clicked object moves, so no
          // chain explosion.
          if (body.bodyType() !== RAPIER.RigidBodyType.Dynamic) {
            body.setBodyType(RAPIER.RigidBodyType.Dynamic, true);
          }
          this.selected = { body, mesh: root };
          SobaViewer.highlight(root, true);
          this.emit("selection-changed", { id: this.meshToEntry.get(root)?.id ?? null });
          // set up a drag plane through the hit point, facing the camera
          this.dragPlane.setFromNormalAndCoplanarPoint(
            camera.getWorldDirection(new THREE.Vector3()).negate(),
            hits[0].point
          );
          this.dragging = true;
          controls.enabled = false;
        }
      }
    }) as EventListener);

    this.listen(dom, "pointermove", ((e: PointerEvent) => {
      if (!this.dragging || !this.selected) return;
      this.setNdc(e);
      this.raycaster.setFromCamera(this.ndc, camera);
      if (this.raycaster.ray.intersectPlane(this.dragPlane, this.dragPoint)) {
        // velocity toward the cursor (a soft impulse, not a teleport)
        const p = this.selected.body.translation();
        const v = {
          x: (this.dragPoint.x - p.x) * 6,
          y: (this.dragPoint.y - p.y) * 6,
          z: (this.dragPoint.z - p.z) * 6,
        };
        this.selected.body.setLinvel(v, true);
        this.selected.body.wakeUp();
      }
    }) as EventListener);

    const endDrag = () => { this.dragging = false; controls.enabled = true; };
    this.listen(dom, "pointerup", endDrag);
    this.listen(dom, "pointerleave", endDrag);

    this.listen(window, "keydown", ((e: KeyboardEvent) => {
      if (e.code === "Space") { e.preventDefault(); this.spawnBall(); }
      if (e.code === "KeyF") this.frameAll(); // re-frame ALL objects on demand
    }) as EventListener);
  }

  spawnBall(): void {
    if (!this.world || !this.camera || !this.scene) return;
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
  private animate(): void {
    if (this.disposed) return;
    this.raf = requestAnimationFrame(() => this.animate());
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
          this.scene!.remove(mesh);
          this.world.removeRigidBody(body);
          this.tempBalls.splice(i, 1);
        }
      }
    }
    if (this.camMove) {
      const m = this.camMove;
      const k = Math.min(1, (performance.now() - m.t0) / m.ms);
      const e = 1 - Math.pow(1 - k, 3); // ease-out cubic
      this.camera!.position.lerpVectors(m.fromPos, m.toPos, e);
      this.controls!.target.lerpVectors(m.fromTarget, m.toTarget, e);
      if (k >= 1) this.camMove = null;
    }
    this.controls!.update();
    this.renderer!.render(this.scene!, this.camera!);

    // throttled stats event (~4 Hz) — discrete, cheap, safe for UI state.
    // Per-frame transforms NEVER cross this boundary (frontend/CLAUDE.md).
    this.statsFrames++;
    const now = performance.now();
    if (now - this.statsLastT >= STATS_INTERVAL_MS) {
      if (this.statsLastT > 0) {
        const fps = Math.round((this.statsFrames * 1000) / (now - this.statsLastT));
        let dynamicBodies = 0;
        for (const body of this.syncMap.keys()) {
          if (body.bodyType() === RAPIER.RigidBodyType.Dynamic) dynamicBodies++;
        }
        this.emit("stats", { fps, objects: this.loadedIds.size, dynamicBodies });
      }
      this.statsLastT = now;
      this.statsFrames = 0;
    }
  }
}
