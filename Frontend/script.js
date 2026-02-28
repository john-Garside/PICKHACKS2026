// ======================
// CONFIG
// ======================
const BACKEND_BASE = "http://127.0.0.1:5000";
const ROADS_ENDPOINT = "/roads";     // optional: draw roads
const SIM_ENDPOINT = "/simulate";    // traffic positions

// Polling rate: slower polling + smooth animation looks better
const SIM_POLL_MS = 750;

// ======================
// STATE
// ======================
let map;
let roadsLayer = L.layerGroup();
let carsLayer = L.layerGroup();

let simTimer = null;
let simRunning = false;
let centeredOnce = false;

// For each car we store:
// marker, from{lat,lon}, to{lat,lon}, t0(ms), t1(ms)
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

// EXACT for your /roads response: {edges:[{start:{lat,lon}, end:{lat,lon}, id:"u-v"}]}
function parseEdges(roadsJson) {
  const edges = roadsJson.edges ?? [];
  return edges.map(e => ({
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

function lerp(a, b, t) {
  return a + (b - a) * t;
}

function clamp01(x) {
  return Math.max(0, Math.min(1, x));
}

// ======================
// MAP INIT
// ======================
function initMap() {
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
    L.polyline(edge.coords, { weight: 3, opacity: 0.5 }).addTo(roadsLayer);
  });

  setStatus(`Roads loaded: ${edges.length} edges`);
}

// ======================
// SMOOTH TRAFFIC
// ======================
async function pollSimOnce() {
  const now = performance.now();
  const simJson = await fetchJSON(SIM_ENDPOINT);
  const list = parseCars(simJson);

  if (!centeredOnce && list.length > 0) {
    map.setView([list[0].lat, list[0].lon], 16);
    centeredOnce = true;
  }

  const seen = new Set();

  for (const c of list) {
    seen.add(c.id);

    if (!cars.has(c.id)) {
      // First time seeing this car: create marker, set from=to=current
      const marker = L.circleMarker([c.lat, c.lon], { radius: 6 }).addTo(carsLayer);
      marker.bindPopup(`Car ${c.id}`);

      cars.set(c.id, {
        marker,
        from: { lat: c.lat, lon: c.lon },
        to: { lat: c.lat, lon: c.lon },
        t0: now,
        t1: now + SIM_POLL_MS
      });
    } else {
      // Update target: move "from" to the marker's current rendered position,
      // then set "to" to the new backend position
      const car = cars.get(c.id);

      // current rendered position (where the marker is right now)
      const ll = car.marker.getLatLng();
      car.from = { lat: ll.lat, lon: ll.lng };

      car.to = { lat: c.lat, lon: c.lon };
      car.t0 = now;
      car.t1 = now + SIM_POLL_MS;
    }
  }

  // Remove cars not present
  for (const [id, car] of cars.entries()) {
    if (!seen.has(id)) {
      carsLayer.removeLayer(car.marker);
      cars.delete(id);
    }
  }

  setStatus(`Traffic update: ${list.length} cars (poll ${SIM_POLL_MS}ms)`);
}

function animateFrame(now) {
  if (!simRunning) return;

  for (const car of cars.values()) {
    const t = clamp01((now - car.t0) / (car.t1 - car.t0 || 1));
    const lat = lerp(car.from.lat, car.to.lat, t);
    const lon = lerp(car.from.lon, car.to.lon, t);
    car.marker.setLatLng([lat, lon]);
  }

  requestAnimationFrame(animateFrame);
}

function startTraffic() {
  if (simTimer) return;

  simRunning = true;
  document.getElementById("btn-toggle-sim").textContent = "Stop Traffic";
  setStatus("Traffic running (smooth)...");

  // Kick off animation loop
  requestAnimationFrame(animateFrame);

  // Poll backend (slower) for new targets
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