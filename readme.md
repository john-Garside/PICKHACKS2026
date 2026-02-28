**1️⃣ Backend Developer Cheat Sheet
**
Goal: Serve road and traffic data to frontend, handle user edits, and call simulation functions.

Files to work on:

backend/app.py

backend/network.py

Endpoints to create:

GET /roads → sends road network as JSON to frontend

POST /edit-road → receives road edits from frontend, updates network

GET /simulate → calls simulation functions and returns traffic positions

Tasks:

Load initial road network from network.py or OSMnx

Convert road network to JSON for frontend

Update road network when frontend sends edits

Call traffic_simulation.py functions to get car positions

Make sure endpoints return JSON in a format frontend can use

Interactions:

Frontend: Receives JSON for map drawing & traffic; receives user edits

Simulation Developer: Calls their functions for traffic positions

Test Steps:

Run python app.py

Open browser → http://127.0.0.1:5000/roads → should return JSON

Test /edit-road with Postman or fetch in frontend

Test /simulate → check JSON of car positions


**2️⃣ Frontend Developer Cheat Sheet
**
Goal: Display map, roads, and traffic; handle user interaction.

Files to work on:

frontend/index.html

frontend/script.js

frontend/style.css

Tasks:

Use Leaflet to display map (index.html)

Fetch road network from GET /roads and draw roads as lines

Fetch traffic positions from GET /simulate and animate cars

Add UI for adding/removing roads and send JSON via POST /edit-road

Make the map responsive and easy to interact with

Interactions:

Backend: Fetches JSON data from backend endpoints; sends user edits to backend

Simulation Developer: Indirectly — receives simulation results from backend

Test Steps:

Open index.html in Live Server (VS Code extension)

Check that roads appear from backend /roads endpoint

Check traffic animation using /simulate endpoint

Test adding/removing roads → ensure /edit-road updates backend


**3️⃣ Simulation / Optimization Developer Cheat Sheet
**
Goal: Simulate traffic and provide data for frontend visualization.

Files to work on:

backend/traffic_simulation.py

Tasks:

Write functions to calculate vehicle positions on road network

Optional: add congestion calculations or traffic optimization

Make functions callable from backend (app.py) endpoints

Return results in JSON-friendly format, e.g.:

[
  {"lat": 40.7585, "lon": -73.9855, "id": 1},
  {"lat": 40.7595, "lon": -73.9865, "id": 2}
]

Interactions:

Backend Developer: Backend calls simulation functions to get traffic data

Frontend Developer: Never calls these functions directly

Test Steps:

Write a test function in traffic_simulation.py that returns sample traffic data

Backend calls this function via /simulate → check JSON output

Ensure the output can be animated by frontend
