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


# Adjust this to change overall car density (Higher = fewer cars)
VOLUME_DENSITY_FACTOR = 500

# Heat normalization: capacity multipliers by road type
HIGHWAY_CAPACITY = {
    "motorway": 6.0,
    "motorway_link": 5.0,
    "trunk": 5.0,
    "trunk_link": 4.0,
    "primary": 3.5,
    "primary_link": 3.0,
    "secondary": 2.5,
    "secondary_link": 2.2,
    "tertiary": 2.0,
    "residential": 1.3,
    "living_street": 1.1,
    "service": 1.0,
    "unclassified": 1.4,
    "road": 1.2,
}


# ============================
# Vehicle spawning
# ============================
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

    # 1) Collect edge weights
    edges_data = []
    total_weight = 0.0

    for u, v, key, data in G.edges(keys=True, data=True):
        base_volume = data.get("traffic_volume", 0)

        # If no probe data, give very small baseline
        if base_volume <= 0:
            base_volume = 1

        # Apply hourly demand multiplier
        adjusted_volume = float(base_volume) * float(volume_multiplier)

        total_weight += adjusted_volume
        edges_data.append((u, v, key, data, adjusted_volume))

    if total_weight <= 0:
        print("No traffic weights available.")
        return

    # 2) Determine total city cars
    BASE_CITY_CARS = 1200  # baseline population
    MAX_CITY_CARS = int(BASE_CITY_CARS * float(volume_multiplier))

    print(f"Spawning {MAX_CITY_CARS} vehicles for this hour.")

    # 3) Distribute proportionally
    for u, v, key, data, adjusted_volume in edges_data:
        share = adjusted_volume / total_weight
        num_to_spawn = int(share * MAX_CITY_CARS)

        if num_to_spawn <= 0:
            continue

        base_speed = data.get("traffic_speed") or data.get("speed_kph", 30)
        if isinstance(base_speed, list):
            base_speed = base_speed[0]

        # Apply hourly speed multiplier once at spawn time
        effective_speed = float(base_speed) * float(speed_multiplier)

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
    """
    Return (lat, lon) at normalized progress along the edge.
    Uses geometry if present (curved roads), else interpolates node-to-node.
    """
    edge_data = G[u][v][key]
    geom = edge_data.get("geometry", None)
    p = max(0.0, min(1.0, float(progress)))

    if geom is not None:
        try:
            line = geom if isinstance(geom, LineString) else LineString(list(geom.coords))
            if line.length > 0:
                pt = line.interpolate(p, normalized=True)
                # Shapely geometry coords are (lon, lat)
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

    Features:
    - Vehicles follow road geometry
    - Intersection types: signal / priority / free-flow
    - Queues at signalized intersections
    - Stop delays at priority intersections
    - Time-of-day scaling of speed and number of cars
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
    
        # GLOBAL SAFETY CHECK: Ensure every edge the vehicle is on has a queue entry
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

            # ===============================
            # Case 1: move along edge
            # ===============================
            if remaining_m < dist_left_on_edge:
                vehicle["progress"] += remaining_m / length_m
                remaining_m = 0
            else:
                # Vehicle reaches the end of the edge
                remaining_m -= dist_left_on_edge
                current_node = v

                # Determine intersection type
                control = G.nodes[current_node].get("control", "none")

                # ===============================
                # SIGNALIZED INTERSECTION
                # ===============================
                if control == "signal":
                    edge_id = (u, v, key)
                    lanes = edge_data.get("lanes", 1)
                    if isinstance(lanes, list): lanes = lanes[0]
                    
                    try:
                        lanes = int(lanes)
                    except (ValueError, TypeError):
                        lanes = 1

                    # Discharge rate: how many cars can pass per simulation step
                    # (Saturation Flow * lanes) / 3600 seconds * step_time
                    discharge_rate = (SATURATION_FLOW_PER_LANE * lanes / 3600) * SIMULATION_STEP_TIME
                    is_green = (signal_timer % SIGNAL_CYCLE) < GREEN_DURATION

                    # 1. If car is not in queue and it's RED or there's a line, join queue
                    if vehicle["id"] not in edge_queues[edge_id]:
                        if not is_green or len(edge_queues[edge_id]) > 0:
                            edge_queues[edge_id].append(vehicle["id"])

                    # 2. If car is in the queue
                    if vehicle["id"] in edge_queues[edge_id]:
                        queue = edge_queues[edge_id]
                        position_in_queue = queue.index(vehicle["id"])

                        # Can this car discharge? 
                        # Only if Green AND it's at the front of the line (within discharge capacity)
                        if is_green and position_in_queue < discharge_rate:
                            queue.pop(position_in_queue) # Leave the queue
                            next_options = list(G.out_edges(current_node, keys=True))
                            # Let it proceed to the next edge logic below...
                        else:
                            # Stay stuck at the end of the road
                            vehicle_delay[vehicle["id"]] = vehicle_delay.get(vehicle["id"], 0) + SIMULATION_STEP_TIME
                            vehicle["progress"] = 0.999
                            remaining_m = 0
                            continue 
                    else:
                        # Not in queue, light is green, just pass through
                        next_options = list(G.out_edges(current_node, keys=True))
                
                
                # ===============================
                # PRIORITY INTERSECTION (stop/yield)
                # ===============================
                elif control == "priority":
                    if not vehicle.get("stopped_at_node"):
                        vehicle["stop_timer"] = 2  # 2 sec stop delay
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
                # Select next edge or teleport if dead-end
                # ===============================
                if next_options:
                    new_u, new_v, new_key = random.choice(next_options)
                else:
                    all_edges = list(G.edges(keys=True))
                    new_u, new_v, new_key = random.choice(all_edges)
                    teleported_this_tick = True

                vehicle["u"], vehicle["v"], vehicle["key"] = new_u, new_v, new_key
                vehicle["progress"] = 0.0

                # update speed for new edge
                new_data = G[new_u][new_v][new_key]
                new_speed = new_data.get("traffic_speed") or new_data.get("speed_kph", 30)
                if isinstance(new_speed, list):
                    new_speed = new_speed[0]
                vehicle["speed_kph"] = float(new_speed)

        # Convert progress to lat/lon
        lat, lon = _point_on_edge(G, vehicle["u"], vehicle["v"], vehicle["key"], vehicle["progress"])

        positions.append({
            "id": vehicle["id"],
            "lat": lat,
            "lon": lon,
            "teleport": teleported_this_tick
        })

    # Optional debug metric
    avg_delay = sum(vehicle_delay.values()) / max(1, len(vehicle_delay))
    print("Average delay:", round(avg_delay, 2), "seconds")

    return positions

# ============================
# Heatmap support (cars per road, normalized by lanes + road class)
# ============================
# Rolla-tuned capacity multipliers
ROAD_CLASS_CAP = {
    "motorway": 10.0,
    "motorway_link": 8.0,
    "trunk": 7.0,
    "trunk_link": 6.0,
    "primary": 5.0,
    "primary_link": 4.5,
    "secondary": 3.5,
    "secondary_link": 3.0,
    "tertiary": 2.7,
    "tertiary_link": 2.4,
    "residential": 1.8,
    "unclassified": 2.0,
    "service": 1.2,
    "living_street": 1.0,
}


def get_road_heat(G):
    """
    Returns:
    {
        "counts": {edge_id: car_count},
        "heat": {edge_id: 0..1.5+ congestion ratio}
    }
    """

    # 1️⃣ Count vehicles per directed edge
    counts = {}
    for veh in vehicles:
        edge_id = f'{veh["u"]}-{veh["v"]}'
        counts[edge_id] = counts.get(edge_id, 0) + 1

    heat = {}

    for u, v, key, data in G.edges(keys=True, data=True):

        edge_id = f"{u}-{v}"
        car_count = counts.get(edge_id, 0)

        # --- lanes ---
        lanes = data.get("lanes", 1)
        if isinstance(lanes, list):
            lanes = lanes[0]
        try:
            lanes = max(1, int(lanes))
        except:
            lanes = 1

        # --- road type ---
        highway = data.get("highway", "residential")
        if isinstance(highway, list):
            highway = highway[0]

        road_multiplier = ROAD_CLASS_CAP.get(str(highway), 2.0)

        # --- edge length in meters ---
        try:
            length_m = float(data.get("length", 100))
        except:
            length_m = 100

        length_factor = max(0.5, length_m / 100.0)

        # --- final capacity ---
        capacity = lanes * road_multiplier * length_factor

        # --- congestion ratio ---
        if capacity <= 0:
            heat_ratio = 0
        else:
            heat_ratio = car_count / capacity

        heat[edge_id] = heat_ratio

    return {
        "counts": counts,
        "heat": heat
    }
