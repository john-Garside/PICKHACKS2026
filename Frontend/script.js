// ======================
// CONFIG
// ======================
const BACKEND_BASE = "http://127.0.0.1:5000";
const ROADS_ENDPOINT = "/roads";        // draw roads
const HEAT_ENDPOINT = "/road-heat";     // road heat data (advances sim if your Flask route does)
const SIM_ENDPOINT  = "/simulate";      // individual car positions
const SIGNALS_ENDPOINT = "/signals";    // 🚦 traffic lights
const STOPS_ENDPOINT   = "/stops";      // 🛑 stop signs (priority intersections)

// How often we update
const HEAT_MS = 800;    // heatmap refresh
const FETCH_MS = 400;   // car fetch refresh
const SIGNALS_MS = 500; // 🚦 light refresh

// Smoothing time constant (ms) for car markers
const CHASE_TAU_MS = 900;

// ======================
// STATE
// ======================
let map;
let roadsLayer;     // base roads (always neutral)
let heatLayer;      // ✅ overlay heat layer (added/removed on toggle)
let carsLayer;
let signalsLayer;   // 🚦 signal markers layer
let stopsLayer;     // 🛑 stop sign markers layer
let canvasRenderer;

let currentHour = 12;

// viewMode: "heat" or "cars"
let viewMode = "heat";

// heat mode state
let heatRunning = false;
let heatTimer = null;

// cars mode state
let carsRunning = false;
let simTimer = null;
let lastFrameMs = null;

// 🚦 signals polling
let signalsRunning = false;
let signalsTimer = null;

let centeredOnce = false;

// Base road polylines (neutral)
const roadLines = new Map(); // edgeId -> polyline

// ✅ Heat overlay polylines (colored by congestion)
const heatLines = new Map(); // edgeId -> polyline

// Store car markers + smoothing state
const cars = new Map();

// 🚦 Store signal markers
const signalMarkers = new Map();

// 🛑 Store stop sign markers
const stopMarkers = new Map();

// ======================
// UTIL
// ======================
function setStatus(msg) {
  const el = document.getElementById("status");
  if (el) el.textContent = msg;
}

async function fetchJSON(path) {
  const res = await fetch(BACKEND_BASE + path);
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`GET ${path} -> ${res.status} ${text}`);
  }
  return res.json();
}

// Backend sends edges with e.coords = [{lat,lon}, ...]
function parseEdges(roadsJson) {
  const edges = roadsJson.edges ?? [];
  return edges
    .map(e => {
      const coords = (e.coords ?? [])
        .map(p => [Number(p.lat), Number(p.lon)])
        .filter(([lat, lon]) => Number.isFinite(lat) && Number.isFinite(lon));
      return { id: e.id ?? "", coords };
    })
    .filter(e => e.id && e.coords.length >= 2);
}

// cars from /simulate
function parseCars(simJson) {
  return (simJson ?? [])
    .map(c => ({ id: c.id, lat: c.lat, lon: c.lon, teleport: !!c.teleport }))
    .filter(c => typeof c.lat === "number" && typeof c.lon === "number");
}

function clamp01(x) {
  return Math.max(0, Math.min(1, x));
}

function lerp(a, b, t) { return a + (b - a) * t; }

function alphaFromDt(dtMs, tauMs) {
  return 1 - Math.exp(-dtMs / Math.max(1, tauMs));
}

// Green (low) -> Yellow (medium) -> Red (high)
function heatColor(t) {
  t = clamp01(t);

  if (t <= 0.5) {
    const a = t / 0.5;
    const r = Math.round(255 * a);
    const g = 255;
    return `rgb(${r},${g},0)`;
  } else {
    const a = (t - 0.5) / 0.5;
    const r = 255;
    const g = Math.round(255 * (1 - a));
    return `rgb(${r},${g},0)`;
  }
}

// ======================
// MAP INIT
// ======================
function initMap() {
  map = L.map("map", { preferCanvas: true }).setView([37.951, -91.771], 14);

  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: "&copy; OpenStreetMap"
  }).addTo(map);

  canvasRenderer = L.canvas({ padding: 0.5 });

  // Base roads always on
  roadsLayer = L.layerGroup().addTo(map);

  // Heat overlay NOT added by default (so cars mode never shows heat)
  heatLayer = L.layerGroup();

  // Cars layer always exists, but only used in cars mode
  carsLayer = L.layerGroup().addTo(map);

  // 🚦 Signals layer (can be toggled)
  signalsLayer = L.layerGroup().addTo(map);

  // 🛑 Stop signs layer (can be toggled)
  stopsLayer = L.layerGroup().addTo(map);
}

// ======================
// ROADS (draw once, store polylines)
// ======================
async function loadRoads() {
  setStatus("Loading roads...");
  const roadsJson = await fetchJSON(ROADS_ENDPOINT);
  const edges = parseEdges(roadsJson);

  roadsLayer.clearLayers();
  heatLayer.clearLayers();
  roadLines.clear();
  heatLines.clear();

  if (edges.length === 0) {
    console.log("ROADS RAW:", roadsJson);
    setStatus("No drawable roads (check console).");
    return;
  }

  const allPoints = [];

  edges.forEach((edge, idx) => {
    allPoints.push(...edge.coords);

    const baseLine = L.polyline(edge.coords, {
      color: "#555",
      weight: 3,
      opacity: 0.55,
      renderer: canvasRenderer
    }).addTo(roadsLayer);

    const heatLine = L.polyline(edge.coords, {
      color: "rgb(0,255,0)",
      weight: 5,
      opacity: 0.0,
      renderer: canvasRenderer
    });

    heatLine.bindTooltip(edge.id ? `Road ${edge.id}` : `Road ${idx}`, { sticky: true });

    roadLines.set(edge.id, baseLine);
    heatLines.set(edge.id, heatLine);
  });

  if (!centeredOnce && allPoints.length > 0) {
    map.fitBounds(L.latLngBounds(allPoints).pad(0.12));
    centeredOnce = true;
  }

  setStatus(`Roads loaded: ${edges.length} edges`);
}

// ======================
// HEAT LAYER SHOW/HIDE
// ======================
function showHeatOverlay() {
  if (heatLayer.getLayers().length === 0) {
    for (const line of heatLines.values()) heatLayer.addLayer(line);
  }
  if (!map.hasLayer(heatLayer)) heatLayer.addTo(map);
}

function hideHeatOverlay() {
  if (map.hasLayer(heatLayer)) map.removeLayer(heatLayer);
}

// ======================
// 🚦 SIGNALS (traffic lights UI)
// ======================
function makeSignalIcon(nsColor, ewColor) {
  const html = `
    <div style="
      width:18px; padding:2px 3px;
      background: rgba(0,0,0,0.55);
      border: 1px solid rgba(255,255,255,0.25);
      border-radius: 6px;
      display:flex; flex-direction:column; gap:2px;
      box-shadow: 0 0 6px rgba(0,0,0,0.35);
    ">
      <div style="
        width:10px; height:10px; border-radius:50%;
        background:${nsColor};
        box-shadow: 0 0 6px ${nsColor};
        margin: 0 auto;
      "></div>
      <div style="
        width:10px; height:10px; border-radius:50%;
        background:${ewColor};
        box-shadow: 0 0 6px ${ewColor};
        margin: 0 auto;
      "></div>
    </div>
  `;

  return L.divIcon({
    className: "",
    html,
    iconSize: [18, 26],
    iconAnchor: [9, 13]
  });
}

async function pollSignalsOnce() {
  const list = await fetchJSON(SIGNALS_ENDPOINT);
  const seen = new Set();

  for (const s of (list ?? [])) {
    const id = String(s.id);
    const lat = Number(s.lat);
    const lon = Number(s.lon);
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) continue;

    seen.add(id);

    const ns = (s.ns === "green") ? "rgb(0,255,0)" : "rgb(255,0,0)";
    const ew = (s.ew === "green") ? "rgb(0,255,0)" : "rgb(255,0,0)";
    const icon = makeSignalIcon(ns, ew);

    if (!signalMarkers.has(id)) {
      const m = L.marker([lat, lon], { icon }).addTo(signalsLayer);

      // Visible only while hovering
      m.bindTooltip(`Signal ${id} • NS: ${s.ns} • EW: ${s.ew}`, {
        direction: "top",
        offset: [0, -14],
        opacity: 0.95,
        sticky: true
      });

      m.on("mouseover", () => m.openTooltip());
      m.on("mouseout",  () => m.closeTooltip());

      signalMarkers.set(id, m);
    } else {
      const m = signalMarkers.get(id);
      m.setLatLng([lat, lon]);
      m.setIcon(icon);
      m.setTooltipContent(`Signal ${id} • NS: ${s.ns} • EW: ${s.ew}`);
    }
  }

  for (const [id, m] of signalMarkers.entries()) {
    if (!seen.has(id)) {
      m.closeTooltip();
      signalsLayer.removeLayer(m);
      signalMarkers.delete(id);
    }
  }
}

function startSignals() {
  if (signalsRunning) return;
  signalsRunning = true;

  pollSignalsOnce().catch(err => console.warn("Signals error:", err));
  signalsTimer = setInterval(() => {
    pollSignalsOnce().catch(err => console.warn("Signals error:", err));
  }, SIGNALS_MS);
}

function stopSignals() {
  signalsRunning = false;
  if (signalsTimer) clearInterval(signalsTimer);
  signalsTimer = null;
}

// ======================
// 🛑 STOP SIGNS (priority intersections)
// ======================
function makeStopSignIcon() {
  const html = `
    <div style="
      width:22px; height:22px;
      display:flex; align-items:center; justify-content:center;
      background:#d40000;
      color:#fff;
      font-weight:800;
      font-size:9px;
      border:2px solid rgba(255,255,255,0.85);
      box-shadow: 0 0 6px rgba(212,0,0,0.55);
      clip-path: polygon(
        30% 0%, 70% 0%,
        100% 30%, 100% 70%,
        70% 100%, 30% 100%,
        0% 70%, 0% 30%
      );
      letter-spacing:0.5px;
      user-select:none;
      pointer-events:none;
    ">STOP</div>
  `;

  return L.divIcon({
    className: "",
    html,
    iconSize: [22, 22],
    iconAnchor: [11, 11]
  });
}

async function loadStopsOnce() {
  const list = await fetchJSON(STOPS_ENDPOINT);
  const icon = makeStopSignIcon();

  // clear previous
  stopsLayer.clearLayers();
  stopMarkers.clear();

  for (const s of (list ?? [])) {
    const id = String(s.id);
    const lat = Number(s.lat);
    const lon = Number(s.lon);
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) continue;

    // NO tooltips
    const m = L.marker([lat, lon], { icon }).addTo(stopsLayer);
    stopMarkers.set(id, m);
  }
}

// ======================
// HEATMAP (style heat overlay lines)
// ======================
async function pollHeatOnce() {
  const heatJson = await fetchJSON(`${HEAT_ENDPOINT}?hour=${currentHour}`);

  const heatMap = heatJson.heat ?? {};
  const countMap = heatJson.counts ?? {};

  const FULL_RED_AT = 1.5;

  for (const [edgeId, line] of heatLines.entries()) {
    const ratio = Number(heatMap[edgeId] ?? 0);
    const c = Number(countMap[edgeId] ?? 0);

    const t = clamp01(ratio / FULL_RED_AT);

    line.setStyle({
      color: heatColor(t),
      weight: 3 + 7 * t,
      opacity: 0.25 + 0.75 * t
    });

    line.bindTooltip(
      `${edgeId} • cars: ${c} • congestion: ${ratio.toFixed(2)}`,
      { sticky: true }
    );
  }

  setStatus("Heatmap updated");
}

// ======================
// CARS VIEW (individual cars)
// ======================
function clearCars() {
  for (const car of cars.values()) carsLayer.removeLayer(car.marker);
  cars.clear();
}

async function pollSimOnce() {
  const simJson = await fetchJSON(`${SIM_ENDPOINT}?hour=${currentHour}`);
  const list = parseCars(simJson);

  const seen = new Set();

  for (const p of list) {
    seen.add(p.id);

    if (!cars.has(p.id)) {
      const marker = L.circleMarker([p.lat, p.lon], {
        radius: 5,
        renderer: canvasRenderer
      }).addTo(carsLayer);

      cars.set(p.id, {
        marker,
        target: { lat: p.lat, lon: p.lon },
        smooth: { lat: p.lat, lon: p.lon }
      });
    } else {
      const car = cars.get(p.id);
      car.target.lat = p.lat;
      car.target.lon = p.lon;

      if (p.teleport) {
        car.smooth.lat = p.lat;
        car.smooth.lon = p.lon;
        car.marker.setLatLng([p.lat, p.lon]);
      }
    }
  }

  for (const [id, car] of cars.entries()) {
    if (!seen.has(id)) {
      carsLayer.removeLayer(car.marker);
      cars.delete(id);
    }
  }

  setStatus(`Cars updated (${list.length})`);
}

function animateCarsFrame(nowMs) {
  if (!carsRunning) return;

  if (lastFrameMs == null) lastFrameMs = nowMs;
  const dt = Math.min(100, Math.max(0, nowMs - lastFrameMs));
  lastFrameMs = nowMs;

  const a = alphaFromDt(dt, CHASE_TAU_MS);

  for (const car of cars.values()) {
    car.smooth.lat = lerp(car.smooth.lat, car.target.lat, a);
    car.smooth.lon = lerp(car.smooth.lon, car.target.lon, a);
    car.marker.setLatLng([car.smooth.lat, car.smooth.lon]);
  }

  requestAnimationFrame(animateCarsFrame);
}

// ======================
// START / STOP (by mode)
// ======================
function startHeatmap() {
  if (heatRunning) return;
  heatRunning = true;

  showHeatOverlay();

  document.getElementById("btn-toggle-sim").textContent = "Stop";
  setStatus("Heatmap running...");

  startSignals();

  pollHeatOnce().catch(err => setStatus(`Heat error: ${err.message}`));
  heatTimer = setInterval(() => {
    pollHeatOnce().catch(err => setStatus(`Heat error: ${err.message}`));
  }, HEAT_MS);
}

function stopHeatmap() {
  heatRunning = false;
  if (heatTimer) clearInterval(heatTimer);
  heatTimer = null;

  stopSignals();
}

function startCars() {
  if (carsRunning) return;
  carsRunning = true;

  hideHeatOverlay();

  document.getElementById("btn-toggle-sim").textContent = "Stop";
  setStatus("Cars view running...");

  startSignals();

  lastFrameMs = null;
  requestAnimationFrame(animateCarsFrame);

  pollSimOnce().catch(err => setStatus(`Sim error: ${err.message}`));
  simTimer = setInterval(() => {
    pollSimOnce().catch(err => setStatus(`Sim error: ${err.message}`));
  }, FETCH_MS);
}

function stopCars() {
  carsRunning = false;
  if (simTimer) clearInterval(simTimer);
  simTimer = null;
  clearCars();

  stopSignals();
}

function startCurrentMode() {
  if (viewMode === "cars") startCars();
  else startHeatmap();
}

function stopCurrentMode() {
  if (viewMode === "cars") stopCars();
  else stopHeatmap();
}

function setMode(newMode) {
  if (newMode === viewMode) return;

  stopCurrentMode();

  if (newMode === "cars") {
    hideHeatOverlay();
    clearCars();
  } else {
    clearCars();
    showHeatOverlay();
    hideHeatOverlay();
  }

  viewMode = newMode;

  const btn = document.getElementById("btn-toggle-sim");
  if (btn) btn.textContent = "Start";

  setStatus(`Mode: ${viewMode === "cars" ? "Cars" : "Heatmap"} (press Start)`);
}

// ======================
// UI
// ======================
function initUI() {
  document.getElementById("btn-refresh").onclick = () =>
    loadRoads().catch(err => setStatus(`Road error: ${err.message}`));

  document.getElementById("btn-toggle-sim").onclick = () => {
    const running = (viewMode === "cars") ? carsRunning : heatRunning;
    if (running) {
      stopCurrentMode();
      document.getElementById("btn-toggle-sim").textContent = "Start";
      setStatus("Stopped.");
    } else {
      startCurrentMode();
    }
  };

  const viewToggle = document.getElementById("viewToggle");
  const viewLabel = document.getElementById("viewLabel");

  if (viewToggle) {
    viewToggle.checked = false;
    if (viewLabel) viewLabel.textContent = "Heatmap";

    hideHeatOverlay();
    setMode("heat");

    viewToggle.onchange = () => {
      if (viewToggle.checked) {
        if (viewLabel) viewLabel.textContent = "Cars";
        setMode("cars");
      } else {
        if (viewLabel) viewLabel.textContent = "Heatmap";
        setMode("heat");
      }
    };
  }

  // 🚦 show/hide traffic lights without stopping sim
  const lightsToggle = document.getElementById("lightsToggle");
  if (lightsToggle) {
    lightsToggle.checked = true;
    lightsToggle.addEventListener("change", () => {
      for (const m of signalMarkers.values()) m.closeTooltip();

      if (lightsToggle.checked) {
        map.addLayer(signalsLayer);
        for (const m of signalMarkers.values()) m.closeTooltip();
      } else {
        map.removeLayer(signalsLayer);
      }
    });
  }

  // 🛑 show/hide stop signs without stopping sim
  const stopsToggle = document.getElementById("stopsToggle");
  if (stopsToggle) {
    stopsToggle.checked = true;
    stopsToggle.addEventListener("change", () => {
      if (stopsToggle.checked) map.addLayer(stopsLayer);
      else map.removeLayer(stopsLayer);
    });
  }
}

// ======================
// BOOT
// ======================
window.addEventListener("load", async () => {
  initMap();
  initUI();

  await loadRoads().catch(err => setStatus(`Road load failed: ${err.message}`));

  // 🛑 load stop signs once (static)
  await loadStopsOnce().catch(err => console.warn("Stops error:", err));

  hideHeatOverlay();
  setStatus("Mode: Heatmap (press Start)");
});