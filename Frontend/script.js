// ======================
// CONFIG
// ======================
const BACKEND_BASE = "http://127.0.0.1:5000";
const ROADS_ENDPOINT = "/roads";     // optional: draw roads
const SIM_ENDPOINT = "/simulate";    // traffic positions

// How often we fetch snapshots
const FETCH_MS = 400;

// Smoothing time constant (ms). Bigger = smoother/less stepping.
// Try 700–1500.
const CHASE_TAU_MS = 900;

// ======================
// STATE
// ======================
let map;
let roadsLayer, carsLayer;
let canvasRenderer;

let currentHour = 12; // Default to noon
let simRunning = false;
let simTimer = null;
let centeredOnce = false;

let lastFrameMs = null;

// id -> {
//   marker,
//   target:{lat,lon},     // latest backend position
//   smooth:{lat,lon}      // filtered target the marker follows
// }
const cars = new Map();

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

function parseEdges(roadsJson) {
  const edges = roadsJson.edges ?? [];
  return edges.map(e => ({
    coords: [
      [e.start.lat, e.start.lon],
      [e.end.lat, e.end.lon]
    ]
  }));
}

function parseCars(simJson) {
  return (simJson ?? [])
    .map(c => ({ id: c.id, lat: c.lat, lon: c.lon }))
    .filter(c => typeof c.lat === "number" && typeof c.lon === "number");
}

function lerp(a, b, t) { return a + (b - a) * t; }

// Exponential smoothing factor from dt and tau
function alphaFromDt(dtMs, tauMs) {
  // alpha = 1 - exp(-dt/tau)
  return 1 - Math.exp(-dtMs / Math.max(1, tauMs));
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
  carsLayer = L.layerGroup().addTo(map);
}

// ======================
// ROADS (optional)
// ======================
async function loadRoads() {
  setStatus("Loading roads...");
  const roadsJson = await fetchJSON(ROADS_ENDPOINT);
  const edges = parseEdges(roadsJson);

  roadsLayer.clearLayers();
  edges.forEach(edge => {
    L.polyline(edge.coords, {
      weight: 3,
      opacity: 0.5,
      renderer: canvasRenderer
    }).addTo(roadsLayer);
  });

  setStatus(`Roads loaded: ${edges.length} edges`);
}

// ======================
// TRAFFIC (chase smoothing)
// ======================
async function pollSimOnce() {
  const simJson = await fetchJSON(`${SIM_ENDPOINT}?hour=${currentHour}`);
  const list = parseCars(simJson);

  if (!centeredOnce && list.length > 0) {
    map.setView([list[0].lat, list[0].lon], 16);
    centeredOnce = true;
  }

  const seen = new Set();

  for (const p of list) {
    seen.add(p.id);

    if (!cars.has(p.id)) {
      const marker = L.circleMarker([p.lat, p.lon], {
        radius: 6,
        renderer: canvasRenderer
      }).addTo(carsLayer);

      marker.bindPopup(`Car ${p.id}`);

      cars.set(p.id, {
        marker,
        target: { lat: p.lat, lon: p.lon },
        smooth: { lat: p.lat, lon: p.lon }
      });
    } else {
      const car = cars.get(p.id);
      car.target.lat = p.lat;
      car.target.lon = p.lon;
    }
  }

  // Remove missing cars
  for (const [id, car] of cars.entries()) {
    if (!seen.has(id)) {
      carsLayer.removeLayer(car.marker);
      cars.delete(id);
    }
  }

  setStatus(`Traffic: ${list.length} cars (fetch ${FETCH_MS}ms, tau ${CHASE_TAU_MS}ms)`);
}

function animateFrame(nowMs) {
  if (!simRunning) return;

  if (lastFrameMs == null) lastFrameMs = nowMs;
  const dt = Math.min(100, Math.max(0, nowMs - lastFrameMs)); // clamp dt for stability
  lastFrameMs = nowMs;

  const a = alphaFromDt(dt, CHASE_TAU_MS);

  for (const car of cars.values()) {
    // Smooth the target toward backend position
    car.smooth.lat = lerp(car.smooth.lat, car.target.lat, a);
    car.smooth.lon = lerp(car.smooth.lon, car.target.lon, a);

    // Place marker at smoothed target
    car.marker.setLatLng([car.smooth.lat, car.smooth.lon]);
  }

  requestAnimationFrame(animateFrame);
}

function startTraffic() {
  if (simRunning) return;

  simRunning = true;
  lastFrameMs = null;

  document.getElementById("btn-toggle-sim").textContent = "Stop Traffic";
  setStatus("Traffic running...");

  requestAnimationFrame(animateFrame);

  pollSimOnce().catch(err => setStatus(`Sim error: ${err.message}`));
  simTimer = setInterval(() => {
    pollSimOnce().catch(err => setStatus(`Sim error: ${err.message}`));
  }, FETCH_MS);
}

function stopTraffic() {
  simRunning = false;
  document.getElementById("btn-toggle-sim").textContent = "Start Traffic";
  if (simTimer) clearInterval(simTimer);
  simTimer = null;
  setStatus("Traffic stopped.");
}

// ======================
// UI
// ======================
function initUI() {
  const hourSlider = document.getElementById('hourSlider');
  const hourLabel = document.getElementById('hourLabel');

  if (hourSlider) {
    hourSlider.oninput = function() {
      currentHour = this.value;
      
      // Update the text label (e.g., "17:00" or "5:00 PM")
      let displayHour = currentHour % 12 || 12;
      let ampm = currentHour >= 12 ? 'PM' : 'AM';
      hourLabel.innerText = `${displayHour}:00 ${ampm}`;
      
      setStatus(`Time set to ${displayHour}:00 ${ampm}`);
    };
  }

  // Your existing buttons...
  document.getElementById("btn-refresh").onclick = () =>
    loadRoads().catch(err => setStatus(`Road error: ${err.message}`));

  document.getElementById("btn-toggle-sim").onclick = () => {
    if (simRunning) stopTraffic();
    else startTraffic();
  };
}

window.addEventListener("load", async () => {
  initMap();
  initUI();
  await loadRoads().catch(err => setStatus(`Road load failed: ${err.message}`));
});