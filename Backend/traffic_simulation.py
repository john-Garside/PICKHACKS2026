import random
from shapely.geometry import LineString

# ============================
# Global state
# ============================
vehicles = []
initialized = False
current_vol_bin = -1  # Tracks if we need to re-spawn cars due to density changes

# Intersection state
edge_queues = {}         # { (u,v,key): [vehicle_ids...] }
vehicle_delay = {}       # { vehicle_id: seconds }
signal_timer = 0         # global simulation timer
SIGNAL_CYCLE = 60        # total cycle seconds
GREEN_DURATION = 30      # seconds green
SATURATION_FLOW_PER_LANE = 1900  # vehicles per hour per lane

# Simulation parameters
SIMULATION_STEP_TIME = 1.0
VOLUME_DENSITY_FACTOR = 500  # used for initial vehicle spawn


def initialize_vehicles(G, volume_multiplier=1.0, speed_multiplier=1.0):
    """
    Fully data-driven vehicle spawning.

    Uses:
    - GeoJSON traffic_volume for spatial weighting
    - Hourly demand multiplier (volume_multiplier)
    - Hourly speed multiplier (speed_multiplier)
    """

    global vehicles
    vehicles = []
    vehicle_id = 0

    # ============================
    # 1️⃣ Collect edge weights
    # ============================
    edges_data = []
    total_weight = 0

    for u, v, key, data in G.edges(keys=True, data=True):

        base_volume = data.get("traffic_volume", 0)

        # If no probe data, give very small baseline
        if base_volume <= 0:
            base_volume = 1

        # Apply hourly demand multiplier
        adjusted_volume = base_volume * volume_multiplier

        total_weight += adjusted_volume
        edges_data.append((u, v, key, data, adjusted_volume))

    if total_weight == 0:
        print("No traffic weights available.")
        return

    # ============================
    # 2️⃣ Determine total city cars
    # ============================

    BASE_CITY_CARS = 1200  # baseline population
    MAX_CITY_CARS = int(BASE_CITY_CARS * volume_multiplier)

    print(f"Spawning {MAX_CITY_CARS} vehicles for this hour.")

    # ============================
    # 3️⃣ Distribute proportionally
    # ============================

    for u, v, key, data, adjusted_volume in edges_data:

        share = adjusted_volume / total_weight
        num_to_spawn = int(share * MAX_CITY_CARS)

        if num_to_spawn <= 0:
            continue

        # Base road speed from GeoJSON or OSM
        base_speed = data.get("traffic_speed") or data.get("speed_kph", 30)

        if isinstance(base_speed, list):
            base_speed = base_speed[0]

        # Apply hourly speed multiplier
        effective_speed = float(base_speed) * speed_multiplier

        for _ in range(num_to_spawn):
            vehicles.append({
                "id": vehicle_id,
                "u": u,
                "v": v,
                "key": key,
                "progress": random.random(),
                "speed_kph": effective_speed
            })
            vehicle_id += 1

    print(f"Simulation loaded with {len(vehicles)} vehicles.")


# ============================
# Helper functions
# ============================
def _edge_length_m(edge_data):
    """Use OSMnx 'length' attribute (meters) for consistent physics."""
    try:
        length = float(edge_data.get("length", 10.0))
    except Exception:
        length = 10.0
    return max(1.0, length)


def _point_on_edge(G, u, v, key, progress):
    """Return (lat, lon) at normalized progress along the edge."""
    edge_data = G[u][v][key]
    geom = edge_data.get("geometry", None)
    p = max(0.0, min(1.0, float(progress)))

    if geom is not None:
        try:
            line = geom if isinstance(geom, LineString) else LineString(list(geom.coords))
            if line.length > 0:
                pt = line.interpolate(p, normalized=True)
                return pt.y, pt.x
        except Exception:
            pass

    u_node = G.nodes[u]
    v_node = G.nodes[v]
    lat = u_node["y"] + p * (v_node["y"] - u_node["y"])
    lon = u_node["x"] + p * (v_node["x"] - u_node["x"])
    return lat, lon


# ============================
# Main simulation step
# ============================
def get_traffic_positions(G, speed_multiplier=1.0, volume_multiplier=1.0):
    """
    Update vehicle positions in the city.
    Features: directional traffic light phases (N-S vs E-W), queues, and delays.
    """
    global initialized, current_vol_bin, vehicles, edge_queues, vehicle_delay, signal_timer

    # Advance global signal timer
    signal_timer += SIMULATION_STEP_TIME

    # Re-initialize vehicles if volume changed
    vol_bin = round(volume_multiplier, 1)
    if not initialized or vol_bin != current_vol_bin:
        initialize_vehicles(G, volume_multiplier, speed_multiplier)
        initialized = True
        current_vol_bin = vol_bin

    positions = []

    for vehicle in vehicles:
        edge_id = (vehicle["u"], vehicle["v"], vehicle["key"])
    
        if edge_id not in edge_queues:
            edge_queues[edge_id] = []

        teleported_this_tick = False
        remaining_m = (vehicle["speed_kph"] * speed_multiplier) / 3.6 * SIMULATION_STEP_TIME

        hops_left = 25  # safety to avoid infinite loops

        while remaining_m > 0 and hops_left > 0:
            hops_left -= 1

            u, v, key = vehicle["u"], vehicle["v"], vehicle["key"]
            edge_data = G[u][v][key]
            length_m = _edge_length_m(edge_data)
            dist_left_on_edge = (1.0 - vehicle["progress"]) * length_m

            # Case 1: move along edge
            if remaining_m < dist_left_on_edge:
                vehicle["progress"] += remaining_m / length_m
                remaining_m = 0
            else:
                # Vehicle reaches the end of the edge
                remaining_m -= dist_left_on_edge
                current_node = v
                control = G.nodes[current_node].get("control", "none")

                # ===============================
                # SIGNALIZED INTERSECTION (FIXED)
                # ===============================
                if control == "signal":
                    # Check directional green light
                    is_green = is_green_for_edge(u, v, key, G, signal_timer)
                    
                    lanes = edge_data.get("lanes", 1)
                    if isinstance(lanes, list): lanes = lanes[0]
                    try:
                        lanes = int(lanes)
                    except (ValueError, TypeError):
                        lanes = 1

                    # Discharge logic
                    discharge_rate = (SATURATION_FLOW_PER_LANE * lanes / 3600) * SIMULATION_STEP_TIME

                    # 1. Join queue if RED or if there's already a line
                    if vehicle["id"] not in edge_queues[edge_id]:
                        if not is_green or len(edge_queues[edge_id]) > 0:
                            edge_queues[edge_id].append(vehicle["id"])

                    # 2. Process Queue
                    if vehicle["id"] in edge_queues[edge_id]:
                        queue = edge_queues[edge_id]
                        position_in_queue = queue.index(vehicle["id"])

                        # Only proceed if GREEN and at the front of the line
                        if is_green and position_in_queue < discharge_rate:
                            queue.pop(position_in_queue)
                            next_options = list(G.out_edges(current_node, keys=True))
                        else:
                            # HOLD AT INTERSECTION
                            vehicle_delay[vehicle["id"]] = vehicle_delay.get(vehicle["id"], 0) + SIMULATION_STEP_TIME
                            vehicle["progress"] = 0.999
                            remaining_m = 0 
                            continue 
                    else:
                        # Light is green and no queue, proceed normally
                        next_options = list(G.out_edges(current_node, keys=True))
                
                # ===============================
                # PRIORITY INTERSECTION
                # ===============================
                elif control == "priority":
                    if not vehicle.get("stopped_at_node"):
                        vehicle["stop_timer"] = 2
                        vehicle["stopped_at_node"] = True

                    if vehicle.get("stop_timer", 0) > 0:
                        vehicle["stop_timer"] -= SIMULATION_STEP_TIME
                        vehicle["progress"] = 0.999
                        remaining_m = 0
                        vehicle_delay[vehicle["id"]] = vehicle_delay.get(vehicle["id"], 0) + SIMULATION_STEP_TIME
                        continue
                    else:
                        vehicle["stopped_at_node"] = False
                        next_options = list(G.out_edges(current_node, keys=True))

                # ===============================
                # FREE-FLOW INTERSECTION
                # ===============================
                else:
                    next_options = list(G.out_edges(current_node, keys=True))

                # ===============================
                # Transition to next road
                # ===============================
                next_options = list(G.out_edges(current_node, keys=True))
                
                if next_options:
                    # Filter out the edge that goes back to where we just came from (u)
                    # next_option format is (v, next_node, key)
                    forward_options = [opt for opt in next_options if opt[1] != u]

                    if forward_options:
                        # Normal intersection: go forward, left, or right
                        new_u, new_v, new_key = random.choice(forward_options)
                    else:
                        # Dead end: the only option is to turn around
                        new_u, new_v, new_key = random.choice(next_options)
                else:
                    # No out-edges at all: teleport to a random spot in the city
                    all_edges = list(G.edges(keys=True))
                    new_u, new_v, new_key = random.choice(all_edges)
                    teleported_this_tick = True

                vehicle["u"], vehicle["v"], vehicle["key"] = new_u, new_v, new_key
                vehicle["progress"] = 0.0
                edge_id = (new_u, new_v, new_key) # Update edge_id for the next loop/queue check

                new_data = G[new_u][new_v][new_key]
                new_speed = new_data.get("traffic_speed") or new_data.get("speed_kph", 30)
                if isinstance(new_speed, list):
                    new_speed = new_speed[0]
                vehicle["speed_kph"] = float(new_speed)

        # Final position calculation
        lat, lon = _point_on_edge(G, vehicle["u"], vehicle["v"], vehicle["key"], vehicle["progress"])
        positions.append({
            "id": vehicle["id"],
            "lat": lat,
            "lon": lon,
            "teleport": teleported_this_tick
        })

    return positions



#Find if traffic light is green for each direction
def is_green_for_edge(u, v, key, G, current_timer):
    node_data = G.nodes[v]
    if node_data.get("control") != "signal":
        return True # Not a signalized intersection
    
    # Calculate cycle position
    cycle_pos = current_timer % SIGNAL_CYCLE
    
    # Simple Phase Logic: 
    # Determine if the incoming road (u -> v) is North-South or East-West
    u_data = G.nodes[u]
    v_data = G.nodes[v]
    
    # Calculate delta y vs delta x to find orientation
    is_north_south = abs(u_data['y'] - v_data['y']) > abs(u_data['x'] - v_data['x'])
    
    if is_north_south:
        # North-South is green for the first half of the cycle
        return cycle_pos < (SIGNAL_CYCLE / 2)
    else:
        # East-West is green for the second half of the cycle
        return cycle_pos >= (SIGNAL_CYCLE / 2)
