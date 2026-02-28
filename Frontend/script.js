// ======================
// CONFIG (matches backend)
// ======================
const BACKEND_BASE = "http://127.0.0.1:5000";
const ROADS_ENDPOINT = "/roads";     // optional: draw roads
const SIM_ENDPOINT = "/simulate";    // traffic positions

// How often to call /simulate
const SIM_POLL_MS = 250;

// ======================
// STATE
// ======================
let map;
let roadsLayer = L.layerGroup();
let carsLayer = L.layerGroup();

let simTimer = null;
let simRunning = false;

// carId -> Leaflet marker
let carMarkers = new Map();

// Auto-center once on the first received car position
let centeredOnce = false;

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

// EXACT for your /roads response: {edges:[{start:{lat,lon}, end:{lat,lon}, id:"u-v"}]}
function parseEdges(roadsJson) {
  const edges = roadsJson.edges ?? [];
  return edges.map(e => ({
    id: e.id,
    coords: [
      [e.start.lat, e.start.lon],
      [e.end.lat, e.end.lon]
    ]
  }));
}

// EXACT for your /simulate response: [{id, lat, lon}, ...]
function parseCars(simJson) {
  return (simJson ?? [])
    .map(c => ({ id: c.id, lat: c.lat, lon: c.lon }))
    .filter(c => typeof c.lat === "number" && typeof c.lon === "number");
}

// ======================
// MAP INIT
// ======================
function initMap() {
  // Default view doesn't really matter now, because we auto-center on first car
  map = L.map("map").setView([37.951, -91.771], 14);

  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: "&copy; OpenStreetMap"
  }).addTo(map);

  roadsLayer.addTo(map);
  carsLayer.addTo(map);
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
    L.polyline(edge.coords, { weight: 3, opacity: 0.6 }).addTo(roadsLayer);
  });

  setStatus(`Roads loaded: ${edges.length} edges`);
}

// ======================
// TRAFFIC
// ======================
async function pollSimOnce() {
  const simJson = await fetchJSON(SIM_ENDPOINT);
  const cars = parseCars(simJson);

  // ✅ FIX: auto-center once so you can SEE the cars even if they aren't in Rolla
  if (!centeredOnce && cars.length > 0) {
    map.setView([cars[0].lat, cars[0].lon], 16);
    centeredOnce = true;
  }

  const seen = new Set();

  cars.forEach(c => {
    seen.add(c.id);

    // Leaflet expects [lat, lng] and your backend uses "lon"
    const ll = L.latLng(c.lat, c.lon);

    let marker = carMarkers.get(c.id);
    if (!marker) {
      // slightly larger marker to be obvious
      marker = L.circleMarker(ll, { radius: 7 }).addTo(carsLayer);
      marker.bindPopup(`Car ${c.id}`);
      carMarkers.set(c.id, marker);
    } else {
      marker.setLatLng(ll);
    }
  });

  // Remove cars that disappeared
  for (const [id, marker] of carMarkers.entries()) {
    if (!seen.has(id)) {
      carsLayer.removeLayer(marker);
      carMarkers.delete(id);
    }
  }

  setStatus(`Traffic update: ${cars.length} cars`);
}

function startTraffic() {
  if (simTimer) return;

  simRunning = true;
  document.getElementById("btn-toggle-sim").textContent = "Stop Traffic";
  setStatus("Traffic running...");

  // Call /simulate immediately, then repeatedly
  pollSimOnce().catch(err => setStatus(`Sim error: ${err.message}`));
  simTimer = setInterval(() => {
    pollSimOnce().catch(err => setStatus(`Sim error: ${err.message}`));
  }, SIM_POLL_MS);
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

  // Optional: load roads on startup
  await loadRoads().catch(err => setStatus(`Road load failed: ${err.message}`));
});