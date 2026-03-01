// ======================
// CONFIG
// ======================
const BACKEND_BASE = "http://127.0.0.1:5000";
const ROADS_ENDPOINT = "/roads";       // draw roads
const HEAT_ENDPOINT = "/road-heat";    // road heat data

// How often we update the heatmap
const HEAT_MS = 800;

// ======================
// STATE
// ======================
let map;
let roadsLayer;
let canvasRenderer;

let currentHour = 12; // number (optional, if you later want hour-based heat)
let heatRunning = false;
let heatTimer = null;
let centeredOnce = false;

// Store road polylines so we can recolor them
const roadLines = new Map(); // edgeId -> polyline

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

function clamp01(x) {
  return Math.max(0, Math.min(1, x));
}

// Green (low) -> Yellow (medium) -> Red (high)
function heatColor(t) {
  t = clamp01(t);

  if (t <= 0.5) {
    // green -> yellow
    const a = t / 0.5;
    const r = Math.round(255 * a);
    const g = 255;
    return `rgb(${r},${g},0)`;
  } else {
    // yellow -> red
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
  roadsLayer = L.layerGroup().addTo(map);
}

// ======================
// ROADS (draw once, store polylines)
// ======================
async function loadRoads() {
  setStatus("Loading roads...");
  const roadsJson = await fetchJSON(ROADS_ENDPOINT);
  const edges = parseEdges(roadsJson);

  roadsLayer.clearLayers();
  roadLines.clear();

  if (edges.length === 0) {
    console.log("ROADS RAW:", roadsJson);
    setStatus("No drawable roads (check console).");
    return;
  }

  const allPoints = [];

  edges.forEach((edge, idx) => {
    allPoints.push(...edge.coords);

    // Start neutral/low heat color until heat loads
    const line = L.polyline(edge.coords, {
      color: "rgb(0,255,0)", // green by default
      weight: 4,
      opacity: 0.6,
      renderer: canvasRenderer
    })
      .bindTooltip(edge.id ? `Road ${edge.id}` : `Road ${idx}`, { sticky: true })
      .addTo(roadsLayer);

    roadLines.set(edge.id, line);
  });

  if (!centeredOnce && allPoints.length > 0) {
    map.fitBounds(L.latLngBounds(allPoints).pad(0.12));
    centeredOnce = true;
  }

  setStatus(`Roads loaded: ${edges.length} edges`);
}

// ======================
// HEATMAP (recolor roads) - smooth blend green->yellow->red
// heatMap values are congestion ratios (ex: 0.2, 0.8, 1.4)
// We scale ratio into 0..1 using FULL_RED_AT.
// ======================
async function pollHeatOnce() {
  const heatJson = await fetchJSON(`${HEAT_ENDPOINT}?hour=${currentHour}`);

  const heatMap = heatJson.heat ?? {};     // edgeId -> congestion ratio (NOT 0..1)
  const countMap = heatJson.counts ?? {};  // edgeId -> car count

  // Congestion ratio where we consider it "fully red"
  // 1.0 = at capacity, 1.5 = clearly overloaded (good for visuals)
  const FULL_RED_AT = 1.5;

  for (const [edgeId, line] of roadLines.entries()) {
    const ratio = Number(heatMap[edgeId] ?? 0); // congestion ratio
    const c = Number(countMap[edgeId] ?? 0);

    // Scale ratio into 0..1 for the color ramp + style ramp
    const t = clamp01(ratio / FULL_RED_AT);

    line.setStyle({
      color: heatColor(t),                 // smooth blend
      weight: 3 + 7 * t,                   // thicker when hotter
      opacity: 0.25 + 0.75 * t             // more visible when hotter
    });

    line.bindTooltip(
      `${edgeId} • cars: ${c} • congestion: ${ratio.toFixed(2)}`,
      { sticky: true }
    );
  }

  setStatus("Heatmap updated");
}

// ======================
// START / STOP HEATMAP
// ======================
function startHeatmap() {
  if (heatRunning) return;
  heatRunning = true;

  document.getElementById("btn-toggle-sim").textContent = "Stop Heatmap";
  setStatus("Heatmap running...");

  pollHeatOnce().catch(err => setStatus(`Heat error: ${err.message}`));
  heatTimer = setInterval(() => {
    pollHeatOnce().catch(err => setStatus(`Heat error: ${err.message}`));
  }, HEAT_MS);
}

function stopHeatmap() {
  heatRunning = false;
  document.getElementById("btn-toggle-sim").textContent = "Start Heatmap";

  if (heatTimer) clearInterval(heatTimer);
  heatTimer = null;

  setStatus("Heatmap stopped.");
}

// ======================
// UI
// ======================
function initUI() {
  const hourSlider = document.getElementById("hourSlider");
  const hourLabel = document.getElementById("hourLabel");

  if (hourSlider) {
    currentHour = Number(hourSlider.value ?? 12);

    const displayHourInit = currentHour % 12 || 12;
    const ampmInit = currentHour >= 12 ? "PM" : "AM";
    if (hourLabel) hourLabel.innerText = `${displayHourInit}:00 ${ampmInit}`;

    hourSlider.oninput = function () {
      currentHour = Number(this.value);

      const displayHour = currentHour % 12 || 12;
      const ampm = currentHour >= 12 ? "PM" : "AM";
      if (hourLabel) hourLabel.innerText = `${displayHour}:00 ${ampm}`;

      setStatus(`Time set to ${displayHour}:00 ${ampm}`);
    };
  }

  document.getElementById("btn-refresh").onclick = () =>
    loadRoads().catch(err => setStatus(`Road error: ${err.message}`));

  // Reuse your existing toggle button, but it now toggles heatmap
  document.getElementById("btn-toggle-sim").onclick = () => {
    if (heatRunning) stopHeatmap();
    else startHeatmap();
  };
}

// ======================
// BOOT
// ======================
window.addEventListener("load", async () => {
  initMap();
  initUI();
  await loadRoads().catch(err => setStatus(`Road load failed: ${err.message}`));
});