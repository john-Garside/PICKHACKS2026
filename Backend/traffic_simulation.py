"""
traffic_simulation.py
---------------------
Handles vehicle spawning, movement (IDM), intersection queuing, and road heat.

New features vs original:
  - Intelligent Driver Model (IDM) for realistic acceleration/following
  - Gap acceptance at priority intersections (with patience & driver variability)
  - Left-turn penalty on single-lane approaches
  - Gaussian speed variation per vehicle
  - Roundabout spawn reduction & staggered progress

Signal logic is delegated to signal_model.py.
Wait-time reporting is handled by wait_report.py.
"""

import json
import os
import random
from collections import defaultdict
from shapely.geometry import LineString

import signal_model

WAIT_STATS_PATH = os.path.join(os.path.dirname(__file__), "wait_stats.json")

# ============================
# Global state
# ============================
vehicles        = []
initialized     = False
current_vol_bin = -1
current_spd_bin = -1

edge_queues   = {}    # { (u,v,key): [vehicle_ids...] }
vehicle_delay = {}    # { vehicle_id: cumulative delay seconds }
signal_timer  = 0.0

SIGNAL_CYCLE             = signal_model.DEFAULT_CYCLE
SATURATION_FLOW_PER_LANE = 1900
SIMULATION_STEP_TIME     = 1.0
VOLUME_DENSITY_FACTOR    = 500

# { node_id: { "total": float, "count": int, "max": float } }
node_wait_stats: dict = {}

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
# IDM acceleration
# ============================
def get_idm_acceleration(v, v_lead, gap, v0):
    """
    Intelligent Driver Model.
    v:      current speed (m/s)
    v_lead: leader speed (m/s)
    gap:    bumper-to-bumper distance (m)
    v0:     desired speed (m/s)
    """
    is_highway = v0 > 20
    a     = 2.0 if is_highway else 1.5
    T     = 1.0 if is_highway else 1.5
    b     = 2.0
    s0    = 2.0
    delta = 4.0

    if gap <= 0.1:
        return -b * 5

    delta_v = v - v_lead
    s_star  = s0 + max(0.0, v * T + v * delta_v / (2.0 * (a * b) ** 0.5))
    return a * (1.0 - (v / v0) ** delta - (s_star / gap) ** 2)


# ============================
# Gap acceptance
# ============================
def get_critical_gap(highway_type, wait_time=0.0):
    base_gaps = {
        "motorway":    2.5,
        "trunk":       3.0,
        "primary":     3.5,
        "secondary":   4.0,
        "tertiary":    4.5,
        "residential": 4.5,
    }
    threshold          = base_gaps.get(str(highway_type), 4.0)
    patience_reduction = min(2.0, wait_time * 0.1)
    driver_variability = random.uniform(-0.5, 0.5)
    return max(1.5, threshold - patience_reduction + driver_variability)


def check_gap_acceptance(G, current_node, vehicle):
    wait_time = vehicle.get("wait_at_intersection_start", 0)
    for u, v, key, data in G.in_edges(current_node, keys=True, data=True):
        highway = data.get("highway", "residential")
        if isinstance(highway, list):
            highway = highway[0]
        if highway not in {"primary", "secondary", "trunk", "motorway"}:
            continue
        major_vehs = [veh for veh in vehicles if veh["u"] == u and veh["v"] == v]
        if not major_vehs:
            continue
        major_vehs.sort(key=lambda x: x["progress"], reverse=True)
        lead = major_vehs[0]
        dist_remaining  = (1.0 - lead["progress"]) * _edge_length_m(data)
        speed_ms        = max(0.1, lead.get("current_speed_ms", 10.0))
        time_to_arrival = dist_remaining / speed_ms
        if time_to_arrival < get_critical_gap(highway, wait_time):
            return False
    return True


# ============================
# Vehicle spawning
# ============================
def initialize_vehicles(G, volume_multiplier=1.0, speed_multiplier=1.0):
    global vehicles
    vehicles = []
    node_wait_stats.clear()
    signal_model.reset()
    vehicle_id = 0

    edges_data   = []
    total_weight = 0.0

    for u, v, key, data in G.edges(keys=True, data=True):
        base_volume = data.get("traffic_volume", 0)
        if base_volume <= 0:
            base_volume = 0.01
        if data.get("junction") == "roundabout":
            base_volume *= 0.15
        adjusted = float(base_volume) * float(volume_multiplier)
        total_weight += adjusted
        edges_data.append((u, v, key, data, adjusted))

    if total_weight <= 0:
        print("No traffic weights available.")
        return

    BASE_CITY_CARS = 1200
    DEMAND_FLOOR   = 0.15
    effective_mult = max(DEMAND_FLOOR, float(volume_multiplier))
    MAX_CITY_CARS  = max(1, int(BASE_CITY_CARS * effective_mult))
    print(f"Spawning {MAX_CITY_CARS} vehicles (vol_mult={volume_multiplier:.2f}).")

    edge_pool = [(u, v, key, data) for u, v, key, data, _ in edges_data]
    weights   = [w for _, _, _, _, w in edges_data]
    sampled   = random.choices(edge_pool, weights=weights, k=MAX_CITY_CARS)

    for i, (u, v, key, data) in enumerate(sampled):
        std        = data.get("traffic_speed_std", 5.0)
        base_speed = random.gauss(data.get("speed_kph", 30), std * 0.5)
        base_speed = max(10.0, base_speed)

        is_roundabout = data.get("junction") == "roundabout"
        progress      = (i % 10) / 10.0 if is_roundabout else random.random()

        vehicles.append({
            "id":               vehicle_id,
            "u":                u,
            "v":                v,
            "key":              key,
            "progress":         progress,
            "speed_kph":        base_speed,
            "current_speed_ms": base_speed / 3.6,
        })
        vehicle_id += 1

    print(f"Simulation loaded with {len(vehicles)} vehicles.")


# ============================
# Helpers
# ============================
def _edge_length_m(edge_data) -> float:
    try:
        return max(1.0, float(edge_data.get("length", 10.0)))
    except Exception:
        return 10.0


def _point_on_edge(G, u, v, key, progress):
    edge_data = G[u][v][key]
    geom = edge_data.get("geometry")
    p    = max(0.0, min(1.0, float(progress)))

    if geom is not None:
        try:
            line = geom if isinstance(geom, LineString) else LineString(list(geom.coords))
            if line.length > 0:
                pt = line.interpolate(p, normalized=True)
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
    entry = signal_model._vehicle_wait_start.get(vehicle_id)
    if entry is None:
        return
    node_id = entry[0]
    wait_s  = signal_model.record_wait_end(vehicle_id, signal_timer)
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
    global initialized, current_vol_bin, current_spd_bin, vehicles
    global edge_queues, vehicle_delay, signal_timer

    signal_timer += float(dt)

    vol_bin = round(volume_multiplier, 2)
    spd_bin = round(speed_multiplier, 2)
    if not initialized or vol_bin != current_vol_bin or spd_bin != current_spd_bin:
        initialize_vehicles(G, volume_multiplier, speed_multiplier)
        initialized     = True
        current_vol_bin = vol_bin
        current_spd_bin = spd_bin

    # Group by edge for IDM leader look-up
    edge_map = defaultdict(list)
    for veh in vehicles:
        edge_map[(veh["u"], veh["v"], veh["key"])].append(veh)

    all_edges_list = list(G.edges(keys=True, data=True))
    positions      = []

    for edge_id, road_vehicles in edge_map.items():
        road_vehicles.sort(key=lambda x: x["progress"], reverse=True)

        u_orig, v_orig, key_orig = edge_id
        edge_data = G[u_orig][v_orig][key_orig]
        length_m  = _edge_length_m(edge_data)

        for i, vehicle in enumerate(road_vehicles):
            v0     = max((vehicle["speed_kph"] * speed_multiplier) / 3.6, 5.0)
            curr_v = vehicle.get("current_speed_ms", v0)

            if i == 0:
                accel = get_idm_acceleration(curr_v, v0, 1000.0, v0)
            else:
                leader = road_vehicles[i - 1]
                gap    = (leader["progress"] - vehicle["progress"]) * length_m - 4.0
                accel  = get_idm_acceleration(curr_v, leader["current_speed_ms"], gap, v0)

            new_v = max(0.0, curr_v + accel * dt)
            vehicle["current_speed_ms"] = new_v
            remaining_m = max(0.0, curr_v * dt + 0.5 * accel * dt ** 2)

            if edge_id not in edge_queues:
                edge_queues[edge_id] = []

            teleported_this_tick = False
            hops_left = 25

            while remaining_m > 0 and hops_left > 0:
                hops_left -= 1
                u, v, key = vehicle["u"], vehicle["v"], vehicle["key"]
                edge_data     = G[u][v][key]
                length_m      = _edge_length_m(edge_data)
                dist_left     = (1.0 - vehicle["progress"]) * length_m

                if remaining_m < dist_left:
                    vehicle["progress"] += remaining_m / length_m
                    remaining_m = 0
                    continue

                remaining_m  -= dist_left
                current_node  = v
                control       = G.nodes[current_node].get("control", "none")

                # ---- Signalized ----
                if control == "signal":
                    is_green = signal_model.is_green_for_edge(u, v, key, G, signal_timer)

                    lanes = edge_data.get("lanes", 1)
                    if isinstance(lanes, list):
                        lanes = lanes[0]
                    try:
                        lanes = int(lanes)
                    except (ValueError, TypeError):
                        lanes = 1

                    effective_lanes = float(lanes)
                    if effective_lanes == 1 and vehicle.get("is_turning_left"):
                        effective_lanes *= 0.5

                    discharge_rate = (SATURATION_FLOW_PER_LANE * effective_lanes / 3600) * float(dt)

                    if vehicle["id"] not in edge_queues[edge_id]:
                        if not is_green or len(edge_queues[edge_id]) > 0:
                            edge_queues[edge_id].append(vehicle["id"])
                            direction = signal_model.edge_direction(u, v, G)
                            signal_model.record_wait_start(vehicle["id"], current_node, direction, signal_timer)
                            vehicle["is_turning_left"] = random.random() < 0.2

                    if vehicle["id"] in edge_queues[edge_id]:
                        queue    = edge_queues[edge_id]
                        pos_in_q = queue.index(vehicle["id"])
                        max_dis  = int(discharge_rate) + (1 if random.random() < (discharge_rate % 1) else 0)

                        if is_green and pos_in_q < max_dis:
                            queue.pop(pos_in_q)
                            _end_wait(vehicle["id"])
                        else:
                            vehicle_delay[vehicle["id"]] = vehicle_delay.get(vehicle["id"], 0) + float(dt)
                            vehicle["progress"]          = min(0.999, 0.97 - pos_in_q * 0.015)
                            vehicle["current_speed_ms"]  = 0.0
                            remaining_m = 0
                            continue

                # ---- Priority ----
                elif control == "priority":
                    major_roads      = {"primary", "secondary", "trunk"}
                    incoming_highway = edge_data.get("highway", "residential")
                    if isinstance(incoming_highway, list):
                        incoming_highway = incoming_highway[0]

                    if str(incoming_highway) in major_roads:
                        vehicle["stopped_at_node"] = False
                        vehicle["stop_timer"]      = 0.0
                    else:
                        if not vehicle.get("stopped_at_node"):
                            vehicle["stop_timer"]      = 1.0
                            vehicle["stopped_at_node"] = True

                        gap_safe = check_gap_acceptance(G, current_node, vehicle)

                        if vehicle.get("stop_timer", 0.0) > 0.0 or not gap_safe:
                            vehicle["stop_timer"]         = max(0.0, vehicle.get("stop_timer", 0.0) - float(dt))
                            vehicle["progress"]           = 0.97
                            vehicle["current_speed_ms"]   = 0.0
                            remaining_m = 0.0
                            vehicle_delay[vehicle["id"]] = vehicle_delay.get(vehicle["id"], 0.0) + float(dt)
                            continue
                        else:
                            vehicle["stopped_at_node"] = False

                # ---- Free-flow: fall through ----

                next_options = list(G.out_edges(current_node, keys=True))
                if next_options:
                    forward = [opt for opt in next_options if opt[1] != u]
                    new_u, new_v, new_key = random.choice(forward if forward else next_options)
                else:
                    new_u, new_v, new_key, _ = random.choice(all_edges_list)
                    teleported_this_tick = True

                vehicle["u"], vehicle["v"], vehicle["key"] = new_u, new_v, new_key
                vehicle["progress"] = 0.0
                edge_id = (new_u, new_v, new_key)

                new_data      = G[new_u][new_v][new_key]
                new_speed_kph = new_data.get("traffic_speed") or new_data.get("speed_kph", 30)
                if isinstance(new_speed_kph, list):
                    new_speed_kph = new_speed_kph[0]
                vehicle["speed_kph"]        = float(new_speed_kph)
                vehicle["current_speed_ms"] = (vehicle["speed_kph"] * speed_multiplier) / 3.6

            lat, lon = _point_on_edge(G, vehicle["u"], vehicle["v"], vehicle["key"], vehicle["progress"])
            positions.append({
                "id":       vehicle["id"],
                "lat":      lat,
                "lon":      lon,
                "teleport": teleported_this_tick,
            })

    signal_model.maybe_rl_update(signal_timer)
    _flush_wait_stats()
    return positions


def _flush_wait_stats():
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
        pass


# ============================
# Road heatmap
# ============================
def get_road_heat(G):
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
