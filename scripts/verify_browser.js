#!/usr/bin/env node
// Headless browser-physics verification for the Phase-11 viewer (DEBT B).
//
// Launches scripts/serve.py for a scene dir on a free port, opens the viewer in
// headless Chrome (puppeteer, swiftshader software WebGL), and asserts:
//   1. zero page errors (uncaught exceptions / console errors);
//   2. object count in the Three.js/Rapier scene matches scene.json;
//   3. every rigid body's LIVE Rapier mass equals scene.json physics.mass_kg
//      (guards the project's founding fix: mass set on the desc BEFORE
//      createRigidBody — a regression would read 0 here);
//   4. bodies load FIXED; after clicking an object through the real pointer
//      path and letting the sim run ~3 s, at least one body is dynamic and NO
//      body has a NaN or |position| > 50 m (no explosion / fall-through);
//   5. "f" re-frames the camera on the whole scene (window.__soba.framedAll).
// Then screenshots the scene and prints a PASS/FAIL table. Exit 0 iff all pass.
//
// Usage (puppeteer is NOT vendored — install it anywhere and point NODE_PATH
// at its node_modules; see frontend/README.md):
//   NODE_PATH=/path/to/node_modules node scripts/verify_browser.js \
//     --scene out/scene_chairs [--screenshot /tmp/chairs.png] [--settle 3000]
//
// --path <url path> (default "/") opens the viewer at another page path on the
// same server, e.g. `--path /jobs/<id>/` with `--scene out/jobs/<id>/scene`:
// the scene dir is what the checks compare against, the path is what the
// browser loads (the job store under out/jobs is reopened by the spawned
// server). With the default path the behaviour is unchanged.
//
// Known artifact: swiftshader logs a WebGL "context lost" style warning; it is
// cosmetic (rendering still works) and is filtered from check 1.

"use strict";

const { spawn } = require("child_process");
const fs = require("fs");
const http = require("http");
const net = require("net");
const os = require("os");
const path = require("path");

const REPO_ROOT = path.resolve(__dirname, "..");

// --------------------------------------------------------------------------
// CLI
// --------------------------------------------------------------------------
function parseArgs(argv) {
  const a = { settle: 3000, timeout: 90000, path: "/" };
  for (let i = 2; i < argv.length; i++) {
    const k = argv[i];
    if (k === "--scene") a.scene = argv[++i];
    else if (k === "--path") a.path = argv[++i];
    else if (k === "--screenshot") a.screenshot = argv[++i];
    else if (k === "--python") a.python = argv[++i];
    else if (k === "--settle") a.settle = Number(argv[++i]);
    else if (k === "--timeout") a.timeout = Number(argv[++i]);
    else { console.error(`unknown arg: ${k}`); process.exit(2); }
  }
  if (!a.scene) {
    console.error("usage: node scripts/verify_browser.js --scene <dir> [--path </jobs/<id>/>] " +
      "[--screenshot <png>] [--python <bin>] [--settle <ms>] [--timeout <ms>]");
    process.exit(2);
  }
  a.scene = path.resolve(REPO_ROOT, a.scene);
  if (!a.path.startsWith("/")) a.path = "/" + a.path;
  if (!a.path.endsWith("/")) a.path += "/";
  a.python = a.python || process.env.SOBA_PYTHON ||
    path.join(os.homedir(), "soba/.venv/bin/python");
  a.screenshot = path.resolve(
    a.screenshot || path.join(os.tmpdir(), `soba_verify_${path.basename(a.scene)}.png`)
  );
  return a;
}

function freePort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.listen(0, "127.0.0.1", () => {
      const port = srv.address().port;
      srv.close(() => resolve(port));
    });
    srv.on("error", reject);
  });
}

function waitForHttp(url, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  return new Promise((resolve, reject) => {
    const tryOnce = () => {
      http.get(url, (res) => {
        res.resume();
        if (res.statusCode === 200) return resolve();
        retry();
      }).on("error", retry);
    };
    const retry = () => {
      if (Date.now() > deadline) return reject(new Error(`server not up: ${url}`));
      setTimeout(tryOnce, 200);
    };
    tryOnce();
  });
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// --------------------------------------------------------------------------
// Main
// --------------------------------------------------------------------------
async function main() {
  const args = parseArgs(process.argv);
  const sceneJson = JSON.parse(fs.readFileSync(path.join(args.scene, "scene.json"), "utf8"));
  const expected = sceneJson.objects || [];
  const expectedById = new Map(expected.map((o) => [o.id, o]));

  // Modern puppeteer is ESM-only, so require() fails; resolve the entry point
  // through NODE_PATH (which ESM bare-specifier resolution ignores) and
  // dynamic-import the file URL instead.
  let puppeteer;
  try {
    const { pathToFileURL } = require("url");
    const mod = await import(pathToFileURL(require.resolve("puppeteer")));
    puppeteer = mod.default || mod;
  } catch {
    console.error("puppeteer not found. Install it OUTSIDE the repo and set NODE_PATH, e.g.:");
    console.error("  mkdir -p ~/tmp/pptr && cd ~/tmp/pptr && npm init -y && npm i puppeteer");
    console.error("  NODE_PATH=~/tmp/pptr/node_modules node scripts/verify_browser.js --scene out/scene_chairs");
    process.exit(2);
  }

  const port = await freePort();
  const server = spawn(args.python, ["scripts/serve.py", "--scene", args.scene, "--port", String(port)],
    { cwd: REPO_ROOT, stdio: ["ignore", "pipe", "pipe"] });
  let serverLog = "";
  server.stdout.on("data", (d) => { serverLog += d; });
  server.stderr.on("data", (d) => { serverLog += d; });
  const killServer = () => { try { server.kill("SIGTERM"); } catch { /* gone */ } };
  process.on("exit", killServer);

  const checks = []; // {name, pass, detail}
  const check = (name, pass, detail = "") => { checks.push({ name, pass, detail }); return pass; };
  let browser;

  try {
    await waitForHttp(`http://127.0.0.1:${port}${args.path}scene.json`, 15000);

    browser = await puppeteer.launch({
      headless: true,
      args: ["--no-sandbox", "--use-gl=swiftshader", "--enable-unsafe-swiftshader",
        "--disable-dev-shm-usage"],
    });
    const page = await browser.newPage();
    await page.setViewport({ width: 1280, height: 800 });

    const pageErrors = [];
    const IGNORE = /context lost|swiftshader|GroupMarkerNotSet|gl_error/i; // known headless-GL noise
    page.on("pageerror", (e) => pageErrors.push(`pageerror: ${e.message}`));
    page.on("console", (m) => {
      if (m.type() === "error" && !IGNORE.test(m.text())) pageErrors.push(`console.error: ${m.text()}`);
    });
    page.on("response", (r) => { // name the URL behind any bare "Failed to load resource"
      if (r.status() >= 400) pageErrors.push(`http ${r.status()}: ${r.url()}`);
    });

    await page.goto(`http://127.0.0.1:${port}${args.path}`, { waitUntil: "domcontentloaded" });

    // Wait until every scene.json object is registered in the viewer.
    await page.waitForFunction(
      (n) => window.__soba && window.__soba.objects.length >= n,
      { timeout: args.timeout }, expected.length
    );
    await sleep(500); // let framing / first paints settle

    const snap = () => page.evaluate(() => window.__soba.objects.map((o) => ({
      id: o.id, massKg: o.massKg, bodyType: o.bodyType, position: o.position,
    })));

    // ---- check 2: object count + ids -------------------------------------
    let objs = await snap();
    check("object count matches scene.json",
      objs.length === expected.length &&
      objs.every((o) => expectedById.has(o.id)),
      `${objs.length}/${expected.length}`);

    // ---- check 3: rapier mass == scene.json mass_kg -----------------------
    const massRows = objs.map((o) => {
      const want = expectedById.get(o.id)?.physics?.mass_kg;
      const ok = want !== undefined && Number.isFinite(o.massKg) &&
        Math.abs(o.massKg - want) <= 1e-3 * Math.max(1, Math.abs(want));
      return { id: o.id, want, got: o.massKg, ok };
    });
    check("rigid-body masses match scene.json",
      massRows.every((r) => r.ok),
      massRows.filter((r) => !r.ok).map((r) => `${r.id}: got ${r.got} want ${r.want}`).join("; ") || "all within 0.1%");

    check("bodies load FIXED (stability contract)",
      objs.every((o) => o.bodyType === "fixed"),
      objs.filter((o) => o.bodyType !== "fixed").map((o) => `${o.id}=${o.bodyType}`).join("; ") || "all fixed");

    // ---- check 5 (do before clicking: click marks user interaction) -------
    await page.keyboard.press("f");
    await sleep(200);
    const framed = await page.evaluate(() => window.__soba.framedAll);
    check("'f' frames all objects (framedAll)", framed === true, `framedAll=${framed}`);

    // ---- check 4: click an object, run ~settle ms, no explosion -----------
    let clickedDynamic = false;
    for (const o of objs) {
      const pos = await page.evaluate((id) => window.__soba.screenPos(id), o.id);
      if (!pos || pos[0] < 0 || pos[1] < 0 || pos[0] > 1280 || pos[1] > 800) continue;
      await page.mouse.click(pos[0], pos[1]);
      await sleep(100);
      const after = await snap();
      if (after.some((x) => x.bodyType === "dynamic")) { clickedDynamic = true; break; }
    }
    check("click wakes a body (fixed -> dynamic)", clickedDynamic);

    await sleep(args.settle); // real time: the rAF loop steps the world
    objs = await snap();
    const bad = objs.filter((o) =>
      !o.position.every(Number.isFinite) ||
      Math.hypot(...o.position) > 50);
    check(`no explosion / fall-through after ${args.settle} ms`,
      bad.length === 0,
      bad.map((o) => `${o.id} at [${o.position.map((v) => v.toFixed(1))}]`).join("; ") ||
      `max |p| = ${Math.max(...objs.map((o) => Math.hypot(...o.position))).toFixed(2)} m`);

    // ---- check 1: page errors ---------------------------------------------
    check("zero page errors", pageErrors.length === 0, pageErrors.slice(0, 3).join(" | ") || "clean");

    // ---- screenshot --------------------------------------------------------
    await page.screenshot({ path: args.screenshot });
    check("screenshot written", fs.existsSync(args.screenshot), args.screenshot);

    // ---- report ------------------------------------------------------------
    console.log(`\nscene: ${args.scene}  (${expected.length} objects, port ${port}, path ${args.path})`);
    console.log("mass table (scene.json vs live Rapier body):");
    for (const r of massRows) {
      console.log(`  ${r.ok ? "ok  " : "FAIL"} ${r.id.padEnd(28)} want ${String(r.want).padEnd(10)} got ${r.got}`);
    }
    console.log("");
    const w = Math.max(...checks.map((c) => c.name.length));
    for (const c of checks) {
      console.log(`  ${c.pass ? "PASS" : "FAIL"}  ${c.name.padEnd(w)}  ${c.detail}`);
    }
    const failed = checks.filter((c) => !c.pass);
    console.log(failed.length ? `\n${failed.length} CHECK(S) FAILED` : "\nALL CHECKS PASSED");
    process.exitCode = failed.length ? 1 : 0;
  } catch (err) {
    console.error("verify_browser: " + (err && err.stack || err));
    if (serverLog) console.error("--- server log ---\n" + serverLog.slice(-2000));
    process.exitCode = 1;
  } finally {
    if (browser) await browser.close().catch(() => {});
    killServer();
  }
}

main();
