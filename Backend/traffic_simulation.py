import random
from shapely.geometry import LineString

# ============================
# Global state
# ============================
vehicles = []
initialized = False
current_vol_bin = -1  # Tracks if we need to re-spawn cars due to density changes

# ============================
# CONFIGURATION
# ============================
# How many seconds pass in the simulation for every backend update
SIMULATION_STEP_TIME = 1.0

# Adjust this to change overall car density (Higher = fewer cars)
VOLUME_DENSITY_FACTOR = 500


# ============================
# Vehicle spawning
# ============================
def initialize_vehicles(G, volume_multiplier=1.0):
    """
    Spawns vehicles across the city based on GeoJSON sample sizes
    and the hourly volume multiplier from the CSV.
    """
    global vehicles
    vehicles = []
    vehicle_id = 0

    # Iterate through every edge (road segment) in the network
    for u, v, key, data in G.edges(keys=True, data=True):
        # 1) Get baseline volume from GeoJSON data
        base_volume = data.get("traffic_volume", 0)

        # 2) Apply hourly multiplier (kept for future use)
        adjusted_volume = base_volume * volume_multiplier

        # 3) Determine how many cars to spawn on this specific road
        # NOTE: you're currently using raw volume logic; adjusted_volume left here if you want it
        volume = data.get("traffic_volume", 0)

        if volume > 0:
            num_to_spawn = int(volume / VOLUME_DENSITY_FACTOR)
        else:
            # Spawn 1 random car on 10% of roads so the map isn't empty
            num_to_spawn = 1 if random.random() < 0.1 else 0

        # 4) Get base speed (from GeoJSON or OSM default)
        speed_kph = data.get("traffic_speed") or data.get("speed_kph", 30)
        if isinstance(speed_kph, list):
            speed_kph = speed_kph[0]

        # 5) Create vehicles
        for _ in range(num_to_spawn):
            vehicles.append({
                "id": vehicle_id,
                "u": u,
                "v": v,
                "key": key,
                "progress": random.random(),  # 0..1 progress along current edge
                "speed_kph": float(speed_kph)
            })
            vehicle_id += 1

    print(f"Simulation loaded with {len(vehicles)} vehicles for this hour.")


# ============================
# Helpers
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
    If geometry exists, follow it (curves). Otherwise, linear interpolate nodes.
    """
    edge_data = G[u][v][key]
    geom = edge_data.get("geometry", None)
    p = max(0.0, min(1.0, float(progress)))

    if geom is not None:
        try:
            line = geom if isinstance(geom, LineString) else LineString(list(geom.coords))
            if line.length > 0:
                pt = line.interpolate(p, normalized=True)
                # geometry points are (lon, lat)
                return pt.y, pt.x
        except Exception:
            pass

    # fallback: straight interpolation between u and v nodes
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
    Updates vehicle positions. Cars follow road geometry.
    Seamless transitions: leftover distance carries into next edge.
    Dead end behavior: instant respawn elsewhere, flagged as teleport=True for that tick.
    """
    global initialized, current_vol_bin

    # If volume changed significantly, respawn cars
    vol_bin = round(volume_multiplier, 1)
    if not initialized or vol_bin != current_vol_bin:
        initialize_vehicles(G, volume_multiplier)
        initialized = True
        current_vol_bin = vol_bin

    positions = []

    for vehicle in vehicles:
        teleported_this_tick = False

        # meters to move this tick
        speed_mps = (vehicle["speed_kph"] * speed_multiplier) / 3.6
        remaining_m = speed_mps * SIMULATION_STEP_TIME

        # safety to avoid infinite loops if weird tiny edges
        hops_left = 25

        while remaining_m > 0 and hops_left > 0:
            hops_left -= 1

            u, v, key = vehicle["u"], vehicle["v"], vehicle["key"]
            edge_data = G[u][v][key]
            length_m = _edge_length_m(edge_data)

            # meters remaining on current edge from current progress to 1.0
            dist_left_on_edge = (1.0 - vehicle["progress"]) * length_m

            if remaining_m < dist_left_on_edge:
                # stays on current edge
                vehicle["progress"] += remaining_m / length_m
                remaining_m = 0.0
            else:
                # reaches end of edge this tick, carry leftover to next edge
                remaining_m -= dist_left_on_edge

                current_node = v
                next_options = list(G.out_edges(current_node, keys=True))

                if next_options:
                    new_u, new_v, new_key = random.choice(next_options)
                else:
                    # ✅ dead-end: respawn to a random edge, mark teleport
                    all_edges = list(G.edges(keys=True))
                    new_u, new_v, new_key = random.choice(all_edges)
                    teleported_this_tick = True

                vehicle["u"], vehicle["v"], vehicle["key"] = new_u, new_v, new_key
                vehicle["progress"] = 0.0

                # update base speed for new edge
                new_data = G[new_u][new_v][new_key]
                new_speed = new_data.get("traffic_speed") or new_data.get("speed_kph", 30)
                if isinstance(new_speed, list):
                    new_speed = new_speed[0]
                vehicle["speed_kph"] = float(new_speed)

        # Convert progress on current edge into lat/lon following geometry
        lat, lon = _point_on_edge(G, vehicle["u"], vehicle["v"], vehicle["key"], vehicle["progress"])

        positions.append({
            "id": vehicle["id"],
            "lat": lat,
            "lon": lon,
            "teleport": teleported_this_tick
        })

    return positions