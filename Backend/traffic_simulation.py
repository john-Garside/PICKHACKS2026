"""
traffic_simulation.py
---------------------
Handles vehicle spawning, movement, intersection queuing, and road heat.

Signal logic is delegated to signal_model.py.
Wait-time reporting is handled by wait_report.py.
"""

import json
import os
import random
from shapely.geometry import LineString

import signal_model

WAIT_STATS_PATH = os.path.join(os.path.dirname(__file__), "wait_stats.json")

# ============================
# Global state
# ============================
vehicles        = []
initialized     = False
current_vol_bin = -1
current_spd_bin = -1   # BUG FIX: track speed_multiplier for re-spawn

edge_queues   = {}    # { (u,v,key): [vehicle_ids...] }
vehicle_delay = {}    # { vehicle_id: cumulative delay seconds }
signal_timer  = 0.0   # global simulation clock (seconds)

SIGNAL_CYCLE             = signal_model.DEFAULT_CYCLE
SATURATION_FLOW_PER_LANE = 1900
SIMULATION_STEP_TIME     = 1.0
VOLUME_DENSITY_FACTOR    = 500

# ============================
# Wait-time stats
# Written here; read by wait_report.py
# { node_id: { "total": float, "count": int, "max": float } }
# ============================
node_wait_stats: dict = {}

# ============================
# Road capacity table
# ============================
ROAD_CLASS_CAP = {
    "motorway":       10.0,
    "motorway_link":   8.0,
    "trunk":           7.0,
    "trunk_link":      6.0,
    "primary":         5.0,
    "primary_link":    4.5,
    "secondary":       3.5,
    "secondary_link":  3.0,
    "tertiary":        2.7,
    "tertiary_link":   2.4,
    "residential":     1.8,
    "unclassified":    2.0,
    "service":         1.2,
    "living_street":   1.0,
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
    node_wait_stats.clear()
    signal_model.reset()

    edges_data   = []
    total_weight = 0.0

    for u, v, key, data in G.edges(keys=True, data=True):
        base_volume = data.get("traffic_volume", 0)
        if base_volume <= 0:
            base_volume = 1.0
        adjusted = float(base_volume) * float(volume_multiplier)
        total_weight += adjusted
        edges_data.append((u, v, key, data, adjusted))

    if total_weight <= 0:
        print("No traffic weights available.")
        return

    # A floor of 0.15 prevents the city from going nearly empty off-peak. (BUG FIX)
    BASE_CITY_CARS   = 1200
    DEMAND_FLOOR     = 0.15
    effective_mult   = max(DEMAND_FLOOR, float(volume_multiplier))
    MAX_CITY_CARS    = max(1, int(BASE_CITY_CARS * effective_mult))
    print(f"Spawning {MAX_CITY_CARS} vehicles for this hour (vol_mult={volume_multiplier:.2f}).")

    for u, v, key, data, adjusted in edges_data:
        share        = adjusted / total_weight
        num_to_spawn = int(share * MAX_CITY_CARS)
        if num_to_spawn <= 0:
            continue

        # Store the RAW base speed — do NOT multiply by speed_multiplier here.
        # The step loop applies speed_multiplier at move time. (BUG FIX)
        base_speed = data.get("traffic_speed") or data.get("speed_kph", 30)
        if isinstance(base_speed, list):
            base_speed = base_speed[0]
        base_speed = float(base_speed)

        for _ in range(num_to_spawn):
            vehicles.append({
                "id":       vehicle_id,
                "u":        u,
                "v":        v,
                "key":      key,
                "progress": random.random(),
                "speed_kph": base_speed,   # raw speed; multiplier applied at step time
            })
            vehicle_id += 1

    print(f"Simulation loaded with {len(vehicles)} vehicles.")


# ============================
# Helpers
# ============================
def _edge_length_m(edge_data) -> float:
    """Use OSMnx 'length' attribute (meters) for consistent physics."""
    try:
        return max(1.0, float(edge_data.get("length", 10.0)))
    except Exception:
        return 10.0


def _point_on_edge(G, u, v, key, progress):
    """
    Return (lat, lon) at normalized progress along the edge.
    Uses geometry if present (curved roads), else interpolates node-to-node.
    """
    edge_data = G[u][v][key]
    geom = edge_data.get("geometry")
    p    = max(0.0, min(1.0, float(progress)))

    if geom is not None:
        try:
            line = geom if isinstance(geom, LineString) else LineString(list(geom.coords))
            if line.length > 0:
                pt = line.interpolate(p, normalized=True)
                # Shapely geometry coords are (lon, lat)
                return pt.y, pt.x
        except Exception:
            pass

    u_n = G.nodes[u]
    v_n = G.nodes[v]
    return (
        u_n["y"] + p * (v_n["y"] - u_n["y"]),
        u_n["x"] + p * (v_n["x"] - u_n["x"]),
    )


def _end_wait(vehicle_id: int):
    """
    Release a vehicle from its signal wait.
    Retrieves wait duration from signal_model and stores it in node_wait_stats.
    """
    entry = signal_model._vehicle_wait_start.get(vehicle_id)
    if entry is None:
        return
    node_id = entry[0]

    wait_s = signal_model.record_wait_end(vehicle_id, signal_timer)
    if wait_s is None or wait_s <= 0:
        return

    stats = node_wait_stats.setdefault(node_id, {"total": 0.0, "count": 0, "max": 0.0})
    stats["total"] += wait_s
    stats["count"] += 1
    stats["max"]    = max(stats["max"], wait_s)


# ============================
# Main simulation step
# ============================
def get_traffic_positions(G, speed_multiplier=1.0, volume_multiplier=1.0, dt=1.0):
    """
    Update vehicle positions in the city.
    Features: directional traffic light phases (N-S vs E-W), queues, and delays.
    """
    global initialized, current_vol_bin, current_spd_bin, vehicles, edge_queues, vehicle_delay, signal_timer

    signal_timer += float(dt)

    # Re-initialize vehicles if volume OR speed changed between hours. (BUG FIX: was vol only)
    vol_bin = round(volume_multiplier, 2)
    spd_bin = round(speed_multiplier, 2)
    if not initialized or vol_bin != current_vol_bin or spd_bin != current_spd_bin:
        initialize_vehicles(G, volume_multiplier, speed_multiplier)
        initialized     = True
        current_vol_bin = vol_bin
        current_spd_bin = spd_bin

    positions = []

    for vehicle in vehicles:
        edge_id = (vehicle["u"], vehicle["v"], vehicle["key"])
        if edge_id not in edge_queues:
            edge_queues[edge_id] = []

        teleported_this_tick = False

        # speed_multiplier applied ONCE here at step time.
        # vehicle["speed_kph"] is always raw base speed. (BUG FIX)
        remaining_m = (vehicle["speed_kph"] * speed_multiplier) / 3.6 * float(dt)
        hops_left   = 25  # safety to avoid infinite loops

        while remaining_m > 0 and hops_left > 0:
            hops_left -= 1

            u, v, key  = vehicle["u"], vehicle["v"], vehicle["key"]
            edge_data  = G[u][v][key]
            length_m   = _edge_length_m(edge_data)
            dist_left  = (1.0 - vehicle["progress"]) * length_m

            # Case 1: Still on this edge
            if remaining_m < dist_left:
                vehicle["progress"] += remaining_m / length_m
                remaining_m = 0
                continue

            # Case 2: Vehicle reaches the end of the edge
            remaining_m  -= dist_left
            current_node  = v
            control       = G.nodes[current_node].get("control", "none")

            # ===============================
            # SIGNALIZED INTERSECTION
            # ===============================
            if control == "signal":
                is_green = signal_model.is_green_for_edge(u, v, key, G, signal_timer)

                lanes = edge_data.get("lanes", 1)
                if isinstance(lanes, list):
                    lanes = lanes[0]
                try:
                    lanes = int(lanes)
                except (ValueError, TypeError):
                    lanes = 1

                discharge_rate = (SATURATION_FLOW_PER_LANE * lanes / 3600) * float(dt)

                if vehicle["id"] not in edge_queues[edge_id]:
                    if not is_green or len(edge_queues[edge_id]) > 0:
                        edge_queues[edge_id].append(vehicle["id"])
                        direction = signal_model.edge_direction(u, v, G)
                        signal_model.record_wait_start(vehicle["id"], current_node, direction, signal_timer)

                if vehicle["id"] in edge_queues[edge_id]:
                    queue    = edge_queues[edge_id]
                    position = queue.index(vehicle["id"])

                    if is_green and position < discharge_rate:
                        queue.pop(position)
                        _end_wait(vehicle["id"])
                        # Fall through to next-edge selection below
                    else:
                        # HOLD AT INTERSECTION
                        vehicle_delay[vehicle["id"]] = vehicle_delay.get(vehicle["id"], 0) + float(dt)
                        vehicle["progress"] = 0.999
                        remaining_m = 0
                        continue
                # else: light is green and no queue — fall through to next-edge selection

            # ===============================
            # PRIORITY INTERSECTION
            # ===============================
            elif control == "priority":
                major_roads = {"primary", "secondary", "trunk"}

                incoming_highway = edge_data.get("highway", "residential")
                if isinstance(incoming_highway, list):
                    incoming_highway = incoming_highway[0]
                incoming_highway = str(incoming_highway)

                if incoming_highway not in major_roads:
                    # Side road must stop briefly
                    if not vehicle.get("stopped_at_node"):
                        vehicle["stop_timer"]      = 2.0
                        vehicle["stopped_at_node"] = True

                    if vehicle.get("stop_timer", 0.0) > 0.0:
                        vehicle["stop_timer"] -= float(dt)
                        vehicle["progress"]    = 0.999
                        remaining_m = 0.0
                        vehicle_delay[vehicle["id"]] = vehicle_delay.get(vehicle["id"], 0.0) + float(dt)
                        continue
                    else:
                        vehicle["stopped_at_node"] = False
                else:
                    # Major road has priority: pass through without stopping
                    vehicle["stopped_at_node"] = False
                    vehicle["stop_timer"]      = 0.0

            # ===============================
            # FREE-FLOW — no delay, fall through
            # ===============================

            # ===============================
            # Transition to next road
            # (BUG FIX: next_options is set ONCE here, after signal/priority logic,
            #  not overridden by a duplicate assignment that discards the above work)
            # ===============================
            next_options = list(G.out_edges(current_node, keys=True))

            if next_options:
                forward = [opt for opt in next_options if opt[1] != u]
                new_u, new_v, new_key = random.choice(forward if forward else next_options)
            else:
                all_edges = list(G.edges(keys=True))
                new_u, new_v, new_key = random.choice(all_edges)
                teleported_this_tick  = True

            vehicle["u"], vehicle["v"], vehicle["key"] = new_u, new_v, new_key
            vehicle["progress"] = 0.0
            edge_id = (new_u, new_v, new_key)

            # Update speed for new edge — store raw base speed (no multiplier). (BUG FIX)
            new_data  = G[new_u][new_v][new_key]
            new_speed = new_data.get("traffic_speed") or new_data.get("speed_kph", 30)
            if isinstance(new_speed, list):
                new_speed = new_speed[0]
            vehicle["speed_kph"] = float(new_speed)   # raw; multiplied at step time

        lat, lon = _point_on_edge(G, vehicle["u"], vehicle["v"], vehicle["key"], vehicle["progress"])
        positions.append({"id": vehicle["id"], "lat": lat, "lon": lon, "teleport": teleported_this_tick})

    # RL update lives in signal_model
    signal_model.maybe_rl_update(signal_timer)
    _flush_wait_stats()

    return positions


def _flush_wait_stats():
    """Write node_wait_stats + signal_model state to wait_stats.json for wait_report.py."""
    try:
        payload = {
            "sim_time":    signal_timer,
            "signal_mode": signal_model.SIGNAL_MODE,
            "rl_updates":  len(signal_model.update_log),
            "node_splits": {str(k): v for k, v in signal_model.node_ns_split.items()},
            "node_cycles": {str(k): v for k, v in signal_model.node_cycle.items()},
            "wait_stats":  {str(k): v for k, v in node_wait_stats.items()},
        }
        tmp = WAIT_STATS_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(payload, f)
        os.replace(tmp, WAIT_STATS_PATH)
    except Exception:
        pass  # never let reporting crash the simulation


# ============================
# Road heatmap
# ============================
def get_road_heat(G):
    """
    Returns:
    {
        "counts": {edge_id: car_count},
        "heat":   {edge_id: 0..1.5+ congestion ratio}
    }
    """
    counts = {}
    for veh in vehicles:
        eid = f'{veh["u"]}-{veh["v"]}'
        counts[eid] = counts.get(eid, 0) + 1

    heat = {}
    for u, v, key, data in G.edges(keys=True, data=True):
        eid = f"{u}-{v}"

        lanes = data.get("lanes", 1)
        if isinstance(lanes, list):
            lanes = lanes[0]
        try:
            lanes = max(1, int(lanes))
        except Exception:
            lanes = 1

        highway = data.get("highway", "residential")
        if isinstance(highway, list):
            highway = highway[0]

        road_mult = ROAD_CLASS_CAP.get(str(highway), 2.0)
        try:
            length_m = float(data.get("length", 100))
        except Exception:
            length_m = 100.0

        length_factor = max(0.5, length_m / 100.0)
        capacity      = lanes * road_mult * length_factor
        car_count     = counts.get(eid, 0)
        heat[eid]     = (car_count / capacity) if capacity > 0 else 0

    return {"counts": counts, "heat": heat}


# ============================
# Traffic light state export (for UI)
# ============================
def get_signal_states(G):
    """
    Returns a list of signalized intersections with current phase.

    Two phases:
      - first half:  North/South green, East/West red
      - second half: East/West green, North/South red

    signal_timer advances when get_traffic_positions() is called.

    Output format:
      [
        {"id": "<node_id>", "lat": float, "lon": float,
         "ns": "green|red", "ew": "green|red",
         "cycle_pos": float, "cycle_len": float},
        ...
      ]
    """
    global signal_timer, SIGNAL_CYCLE

    cycle_pos = signal_timer % SIGNAL_CYCLE
    ns_green  = cycle_pos < (SIGNAL_CYCLE / 2)

    ns = "green" if ns_green else "red"
    ew = "green" if not ns_green else "red"

    out = []
    for node_id, data in G.nodes(data=True):
        if data.get("control") != "signal":
            continue

        lat = data.get("y")
        lon = data.get("x")
        if lat is None or lon is None:
            continue

        out.append({
            "id":        str(node_id),
            "lat":       float(lat),
            "lon":       float(lon),
            "ns":        ns,
            "ew":        ew,
            "cycle_pos": float(cycle_pos),
            "cycle_len": float(SIGNAL_CYCLE),
        })

    return out
