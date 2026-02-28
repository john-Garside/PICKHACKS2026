// ======================
// CONFIG
// ======================
const BACKEND_BASE = "http://127.0.0.1:5000";
const ROADS_ENDPOINT = "/roads";     // draw roads
const SIM_ENDPOINT = "/simulate";    // traffic positions

// How often we fetch snapshots
const FETCH_MS = 400;

// Smoothing time constant (ms). Bigger = smoother/less stepping.
const CHASE_TAU_MS = 900;

// ======================
// STATE
// ======================
let map;
let roadsLayer, carsLayer;
let canvasRenderer;

let currentHour = 12; // number
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

// ✅ UPDATED: backend sends edges with e.coords = [{lat,lon}, ...]
function parseEdges(roadsJson) {
  const edges = roadsJson.edges ?? [];
  return edges
    .map(e => {
      const coords = (e.coords ?? [])
        .map(p => [Number(p.lat), Number(p.lon)])
        .filter(([lat, lon]) => Number.isFinite(lat) && Number.isFinite(lon));

      return { id: e.id ?? "", coords };
    })
    .filter(e => e.coords.length >= 2);
}

// ✅ UPDATED: include teleport flag from backend
function parseCars(simJson) {
  return (simJson ?? [])
    .map(c => ({ id: c.id, lat: c.lat, lon: c.lon, teleport: !!c.teleport }))
    .filter(c => typeof c.lat === "number" && typeof c.lon === "number");
}

function lerp(a, b, t) { return a + (b - a) * t; }

function alphaFromDt(dtMs, tauMs) {
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
// ROADS (draw edges + fit to bounds)
// ======================
async function loadRoads() {
  setStatus("Loading roads...");
  const roadsJson = await fetchJSON(ROADS_ENDPOINT);

  const edges = parseEdges(roadsJson);

  roadsLayer.clearLayers();

  if (edges.length === 0) {
    console.log("ROADS RAW:", roadsJson);
    setStatus("No drawable roads (check console for ROADS RAW).");
    return;
  }

  const allPoints = [];

  edges.forEach((edge, idx) => {
    allPoints.push(...edge.coords);

    L.polyline(edge.coords, {
      weight: 5,
      opacity: 0.9,
      // If you want canvas for performance, uncomment:
      // renderer: canvasRenderer
    })
      .bindTooltip(edge.id ? `Road ${edge.id}` : `Road ${idx}`, { sticky: true })
      .addTo(roadsLayer);
  });

  // Zoom to road network so you can actually see it
  const bounds = L.latLngBounds(allPoints);
  map.fitBounds(bounds.pad(0.12));
  centeredOnce = true;

  setStatus(`Roads loaded: ${edges.length} edges`);
}

// ======================
// TRAFFIC (chase smoothing + teleport snap)
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

      // ✅ If backend says this was a respawn/dead-end teleport:
      // hard-snap smoothing so it doesn't "fly" across the map.
      if (p.teleport) {
        car.smooth.lat = p.lat;
        car.smooth.lon = p.lon;
        car.marker.setLatLng([p.lat, p.lon]);
      }
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
  const hourSlider = document.getElementById("hourSlider");
  const hourLabel = document.getElementById("hourLabel");

  if (hourSlider) {
    currentHour = Number(hourSlider.value ?? 12);

    const displayHourInit = currentHour % 12 || 12;
    const ampmInit = currentHour >= 12 ? "PM" : "AM";
    if (hourLabel) hourLabel.innerText = `${displayHourInit}:00 ${ampmInit}`;

    hourSlider.oninput = function () {
      currentHour = Number(this.value);

      let displayHour = currentHour % 12 || 12;
      let ampm = currentHour >= 12 ? "PM" : "AM";
      if (hourLabel) hourLabel.innerText = `${displayHour}:00 ${ampm}`;

      setStatus(`Time set to ${displayHour}:00 ${ampm}`);
    };
  }

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