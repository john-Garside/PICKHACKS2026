# PickHacks 2026: Smart City Traffic Simulator

## Project Overview
This project is a **smart city traffic and road planning simulator**.  
It uses **Python (Flask + NetworkX + OSMnx) for backend and traffic simulation**, and **Leaflet.js for frontend map visualization**.  
Users can view roads, simulate traffic, and interactively add/remove roads to see traffic changes in real-time.

---

## Table of Contents
1. [Team Roles](#team-roles)

---

## Team Roles

### Backend Developer
- **Goal:** Serve road and traffic data to frontend, handle user edits, call simulation functions.
- **Files:** `backend/app.py`, `backend/network.py`
- **Tasks:**
  - Load initial road network (from `network.py` or OSMnx) and convert to JSON
  - Create endpoints:
    - `GET /roads` → returns road network JSON
    - `POST /edit-road` → updates road network based on frontend edits
    - `GET /simulate` → returns current traffic positions
  - Update road network when frontend sends edits
  - Call simulation functions from `traffic_simulation.py`
- **Interactions:**
  - **Frontend:** Receives JSON for map and traffic; receives user edits
  - **Simulation Developer:** Calls their functions for traffic positions
- **Testing Steps:**
  1. Run `python app.py`
  2. Open browser → `http://127.0.0.1:5000/roads` should return JSON
  3. Test `/edit-road` with Postman or fetch in frontend
  4. Test `/simulate` → check JSON of car positions

---

### Frontend Developer
- **Goal:** Display map, roads, and traffic; handle user interactions
- **Files:** `frontend/index.html`, `frontend/script.js`, `frontend/style.css`
- **Tasks:**
  - Display interactive map using Leaflet.js
  - Fetch road network from `/roads` and draw roads
  - Fetch traffic positions from `/simulate` and animate cars
  - Add UI for adding/removing roads; send edits to backend (`POST /edit-road`)
- **Interactions:**
  - **Backend:** Fetches JSON data from backend endpoints; sends road edits
  - **Simulation Developer:** Indirectly — receives simulation results via backend
- **Testing Steps:**
  1. Open `index.html` with Live Server
  2. Check that roads appear from `/roads` endpoint
  3. Check traffic animation using `/simulate` endpoint
  4. Test adding/removing roads → ensure `/edit-road` updates backend

---

### Simulation / Optimization Developer
- **Goal:** Calculate traffic, congestion, and optional optimization
- **Files:** `backend/traffic_simulation.py`
- **Tasks:**
  - Write functions to calculate vehicle positions on the road network
  - Optional: calculate congestion or optimize traffic lights
  - Provide data in JSON-friendly format for backend to send to frontend
  - Example JSON output:
    ```json
    [
      {"lat": 40.7585, "lon": -73.9855, "id": 1},
      {"lat": 40.7595, "lon": -73.9865, "id": 2}
    ]
    ```
- **Interactions:**
  - **Backend:** Backend calls simulation functions
  - **Frontend:** Receives simulation data only through backend endpoints
- **Testing Steps:**
  1. Write a test function returning sample traffic data
  2. Call it via `/simulate` endpoint → ensure valid JSON
  3. Check frontend can animate vehicle positions
