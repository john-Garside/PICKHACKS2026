import random
from shapely.geometry import LineString
from collections import defaultdict

# ============================
# Global state
# ============================
vehicles = []
initialized = False
current_vol_bin = -1   # tracks volume_multiplier for re-spawn
current_spd_bin = -1   # tracks speed_multiplier for re-spawn (BUG FIX: was missing)

# Intersection state
edge_queues = {}         # { (u,v,key): [vehicle_ids...] }
vehicle_delay = {}       # { vehicle_id: seconds }
signal_timer = 0         # global simulation timer
SIGNAL_CYCLE = 60        # total cycle seconds
GREEN_DURATION = 30      # seconds green
SATURATION_FLOW_PER_LANE = 1900  # vehicles per hour per lane

# Simulation parameters
SIMULATION_STEP_TIME = 1.0

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

    NOTE: vehicles store the RAW base speed here.
          The speed_multiplier is applied in the simulation step only,
          so it is never applied twice. (BUG FIX)
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

    # 2) Determine total city cars.
    #    Rolla population ~20k; even at 3am there are meaningful vehicles present.
    #    A floor of 0.15 prevents the city from going nearly empty off-peak. (BUG FIX)
    BASE_CITY_CARS = 1200
    DEMAND_FLOOR = 0.15
    effective_multiplier = max(DEMAND_FLOOR, float(volume_multiplier))
    MAX_CITY_CARS = max(1, int(BASE_CITY_CARS * effective_multiplier))

    print(f"Spawning {MAX_CITY_CARS} vehicles for this hour (vol_mult={volume_multiplier:.2f}).")

    # 3) Distribute proportionally
    for u, v, key, data, adjusted_volume in edges_data:
        share = adjusted_volume / total_weight
        num_to_spawn = int(share * MAX_CITY_CARS)

        if num_to_spawn <= 0:
            continue

        # Store the RAW base speed — do NOT multiply by speed_multiplier here.
        # The step loop applies speed_multiplier at move time, so it stays consistent
        # across edge hops and hour changes without requiring re-spawning. (BUG FIX)
        base_speed = data.get("traffic_speed") or data.get("speed_kph", 30)
        if isinstance(base_speed, list):
            base_speed = base_speed[0]
        base_speed = float(base_speed)

        for _ in range(num_to_spawn):
            vehicles.append({
                "id": vehicle_id,
                "u": u,
                "v": v,
                "key": key,
                "progress": random.random(),
                "speed_kph": base_speed,   # raw speed; multiplier applied at step time
                "current_speed_ms": base_speed / 3.6
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
def get_traffic_positions(G, speed_multiplier=1.0, volume_multiplier=1.0, dt=1.0):
    """
    Update vehicle positions using IDM for acceleration and 
    maintaining existing intersection/queuing logic.
    """
    global initialized, current_vol_bin, current_spd_bin, vehicles, edge_queues, vehicle_delay, signal_timer

    signal_timer += float(dt)

    vol_bin = round(volume_multiplier, 2)
    spd_bin = round(speed_multiplier, 2)
    if not initialized or vol_bin != current_vol_bin or spd_bin != current_spd_bin:
        initialize_vehicles(G, volume_multiplier, speed_multiplier)
        # Ensure new vehicles have a speed state
        for v in vehicles:
            if "current_speed_ms" not in v:
                v["current_speed_ms"] = (v["speed_kph"] * speed_multiplier) / 3.6
        initialized = True
        current_vol_bin = vol_bin
        current_spd_bin = spd_bin

    # 1. Group vehicles by edge and sort them so we know who is in front
    edge_map = defaultdict(list)
    for v in vehicles:
        edge_map[(v["u"], v["v"], v["key"])].append(v)

    positions = []

    # 2. Iterate through each road segment
    for edge_id, road_vehicles in edge_map.items():
        # Sort by progress (highest progress first = leader)
        road_vehicles.sort(key=lambda x: x["progress"], reverse=True)
        
        u_orig, v_orig, key_orig = edge_id
        edge_data = G[u_orig][v_orig][key_orig]
        length_m = _edge_length_m(edge_data)

        for i, vehicle in enumerate(road_vehicles):
            # Desired speed for this specific road
            v0 = (vehicle["speed_kph"] * speed_multiplier) / 3.6
            curr_v = vehicle.get("current_speed_ms", v0)

            # Determine IDM acceleration based on car in front
            if i == 0:
                # No car in front on THIS edge. 
                # (Intersection logic handles the "virtual stop" below)
                accel = 1.5 * (1 - (curr_v / v0)**4)
            else:
                leader = road_vehicles[i-1]
                # Distance between cars
                gap = (leader["progress"] - vehicle["progress"]) * length_m - 4.0 # 4m car buffer
                accel = get_idm_acceleration(curr_v, leader["current_speed_ms"], gap, v0)

            # Update velocity and calculate how far the car wants to move
            new_v = max(0, curr_v + accel * dt)
            vehicle["current_speed_ms"] = new_v
            # Displacement formula: d = vt + 0.5at^2
            remaining_m = (curr_v * dt) + (0.5 * accel * (dt**2))
            remaining_m = max(0, remaining_m)

            # --- FROM HERE DOWN: Your original logic remains the same ---
            if edge_id not in edge_queues:
                edge_queues[edge_id] = []

            teleported_this_tick = False
            hops_left = 25 

            while remaining_m > 0 and hops_left > 0:
                hops_left -= 1
                u, v, key = vehicle["u"], vehicle["v"], vehicle["key"]
                edge_data = G[u][v][key]
                length_m = _edge_length_m(edge_data)
                dist_left_on_edge = (1.0 - vehicle["progress"]) * length_m

                if remaining_m < dist_left_on_edge:
                    vehicle["progress"] += remaining_m / length_m
                    remaining_m = 0
                else:
                    remaining_m -= dist_left_on_edge
                    current_node = v
                    control = G.nodes[current_node].get("control", "none")

                    if control == "signal":
                        is_green = is_green_for_edge(u, v, key, G, signal_timer)
                        lanes = edge_data.get("lanes", 1)
                        if isinstance(lanes, list): lanes = lanes[0]
                        try: lanes = int(lanes)
                        except: lanes = 1

                        discharge_rate = (SATURATION_FLOW_PER_LANE * lanes / 3600) * float(dt)

                        if vehicle["id"] not in edge_queues[edge_id]:
                            if not is_green or len(edge_queues[edge_id]) > 0:
                                edge_queues[edge_id].append(vehicle["id"])

                        if vehicle["id"] in edge_queues[edge_id]:
                            queue = edge_queues[edge_id]
                            pos_in_q = queue.index(vehicle["id"])
                            if is_green and pos_in_q < discharge_rate:
                                queue.pop(pos_in_q)
                            else:
                                vehicle_delay[vehicle["id"]] = vehicle_delay.get(vehicle["id"], 0) + float(dt)
                                vehicle["progress"] = 0.999
                                remaining_m = 0 
                                vehicle["current_speed_ms"] = 0 # IDM reset
                                continue 
                        
                    elif control == "priority":
                        major_roads = {"primary", "secondary", "trunk"}
                        incoming_highway = edge_data.get("highway", "residential")
                        if isinstance(incoming_highway, list): incoming_highway = incoming_highway[0]
                        coming_from_major = str(incoming_highway) in major_roads

                        if coming_from_major:
                            vehicle["stopped_at_node"] = False
                            vehicle["stop_timer"] = 0.0
                        else:
                            if not vehicle.get("stopped_at_node"):
                                vehicle["stop_timer"] = 2.0
                                vehicle["stopped_at_node"] = True

                            if vehicle.get("stop_timer", 0.0) > 0.0:
                                vehicle["stop_timer"] -= float(dt)
                                vehicle["progress"] = 0.999
                                remaining_m = 0.0
                                vehicle["current_speed_ms"] = 0 # IDM reset
                                vehicle_delay[vehicle["id"]] = vehicle_delay.get(vehicle["id"], 0.0) + float(dt)
                                continue
                            else:
                                vehicle["stopped_at_node"] = False

                    # Transition to next road
                    next_options = list(G.out_edges(current_node, keys=True))
                    if next_options:
                        forward_options = [opt for opt in next_options if opt[1] != u]
                        if forward_options:
                            new_u, new_v, new_key = random.choice(forward_options)
                        else:
                            new_u, new_v, new_key = random.choice(next_options)
                    else:
                        all_edges = list(G.edges(keys=True))
                        new_u, new_v, new_key = random.choice(all_edges)
                        teleported_this_tick = True

                    vehicle["u"], vehicle["v"], vehicle["key"] = new_u, new_v, new_key
                    vehicle["progress"] = 0.0
                    edge_id = (new_u, new_v, new_key)
                    new_data = G[new_u][new_v][new_key]
                    new_speed = new_data.get("traffic_speed") or new_data.get("speed_kph", 30)
                    if isinstance(new_speed, list): new_speed = new_speed[0]
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
        return cycle_pos < (SIGNAL_CYCLE / 2)
    else:
        return cycle_pos >= (SIGNAL_CYCLE / 2)


# ============================
# Traffic light state export (for UI)
# ============================
def get_signal_states(G):
    """
    Returns a list of signalized intersections with current phase.
    Two phases:
      - first half:  North/South green, East/West red
      - second half: East/West green, North/South red
    """
    global signal_timer

    cycle_pos = signal_timer % SIGNAL_CYCLE
    ns_green = cycle_pos < (SIGNAL_CYCLE / 2)
    ew_green = not ns_green

    signals = []
    for node, data in G.nodes(data=True):
        if data.get("control") == "signal":
            signals.append({
                "id": node,
                "lat": float(data["y"]),
                "lon": float(data["x"]),
                "ns": "green" if ns_green else "red",
                "ew": "green" if ew_green else "red",
                "cycle_pos": float(cycle_pos),
                "cycle_len": float(SIGNAL_CYCLE),
            })
    return signals


# ============================
# Heatmap support (cars per road, normalized by lanes + road class)
# ============================
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

    # 1) Count vehicles per directed edge
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
        except Exception:
            lanes = 1

        # --- road type ---
        highway = data.get("highway", "residential")
        if isinstance(highway, list):
            highway = highway[0]

        road_multiplier = ROAD_CLASS_CAP.get(str(highway), 2.0)

        # --- edge length in meters ---
        try:
            length_m = float(data.get("length", 100))
        except Exception:
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


# ============================
# Signal state export (for UI)
# ============================
def get_signal_states(G):
    """Return traffic-signal marker data for the frontend.

    Output format:
      [
        {"id": "<node_id>", "lat": <float>, "lon": <float>, "ns": "green|red", "ew": "green|red"},
        ...
      ]

    The phase is global (signal_timer) and matches is_green_for_edge():
    first half of the cycle is N/S green, second half is E/W green.

    signal_timer advances when get_traffic_positions() is called (e.g., /simulate or /road-heat).
    """
    global signal_timer, SIGNAL_CYCLE

    cycle_pos = signal_timer % SIGNAL_CYCLE
    ns_green = cycle_pos < (SIGNAL_CYCLE / 2)
    ew_green = not ns_green

    ns = "green" if ns_green else "red"
    ew = "green" if ew_green else "red"

    out = []
    for node_id, data in G.nodes(data=True):
        if data.get("control") != "signal":
            continue

        # OSMnx stores lon in 'x' and lat in 'y'
        lat = data.get("y")
        lon = data.get("x")
        if lat is None or lon is None:
            continue

        out.append({
            "id": str(node_id),
            "lat": float(lat),
            "lon": float(lon),
            "ns": ns,
            "ew": ew,
        })

    return out

def get_idm_acceleration(v, v_lead, gap, v0):
    """
    Calculates acceleration based on IDM formula.
    v: current speed (m/s)
    v_lead: speed of car in front (m/s)
    gap: distance to car in front (m)
    v0: desired speed (m/s) based on speed_multiplier
    """
    # Parameters for realistic driving
    a = 1.5       # Max acceleration m/s^2
    b = 2.0       # Comfortable deceleration m/s^2
    T = 1.5       # Desired time headway (s)
    s0 = 2.0      # Minimum jam distance (m)
    delta = 4.0   # Acceleration exponent
    
    # Safety check for collisions
    if gap <= 0.1: return -b * 5 

    delta_v = v - v_lead
    # Desired gap s*
    s_star = s0 + max(0, (v * T) + (v * delta_v) / (2 * (a * b)**0.5))
    
    # IDM Formula
    acceleration = a * (1 - (v / v0)**delta - (s_star / gap)**2)
    return acceleration