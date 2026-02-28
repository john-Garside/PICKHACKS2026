import osmnx as ox
import networkx as nx


def calculate_road_score(data):
    # Higher score = less likely a car will use this road
    # (used as a cost/weight for shortest path routing)

    length = data.get('length', 100)  # meters

    # Use speed_kph if available, otherwise estimate from road type
    speed = data.get('speed_kph', None)
    if speed is None:
        highway_speeds = {
            'motorway': 100,
            'trunk': 80,
            'primary': 60,
            'secondary': 50,
            'tertiary': 40,
            'residential': 25,
            'service': 15,
            'unknown': 30
        }
        highway = data.get('highway', 'unknown')
        if isinstance(highway, list):
            highway = highway[0]
        speed = highway_speeds.get(highway, 30)

    if isinstance(speed, list):
        speed = float(speed[0])
    speed = float(speed)

    # Base travel time in seconds
    travel_time = (length / 1000) / speed * 3600

    # Penalize roads with fewer lanes (more likely to congest)
    lanes = data.get('lanes', 1)
    if isinstance(lanes, list):
        lanes = int(lanes[0])
    lanes = int(lanes)
    lane_penalty = 1 / lanes  # fewer lanes = higher cost

    # Penalize lower quality road types
    highway_penalty = {
        'motorway': 0.8,
        'trunk': 0.9,
        'primary': 1.0,
        'secondary': 1.1,
        'tertiary': 1.3,
        'residential': 1.6,
        'service': 2.0,
        'unknown': 1.5
    }
    highway = data.get('highway', 'unknown')
    if isinstance(highway, list):
        highway = highway[0]
    penalty = highway_penalty.get(highway, 1.5)

    score = travel_time * lane_penalty * penalty
    return score


def load_network():
    G = ox.graph_from_place("Rolla, Missouri, USA", network_type="drive")

    # Add speed and travel time data from OSMnx
    G = ox.add_edge_speeds(G)
    G = ox.add_edge_travel_times(G)

    # Calculate and store road score on every edge
    for u, v, data in G.edges(data=True):
        data['road_score'] = calculate_road_score(data)

    return G


def network_to_json(G):
    edges = []
    for u, v, data in G.edges(data=True):
        u_data = G.nodes[u]
        v_data = G.nodes[v]

        # If the edge has geometry (curved road), use it
        if 'geometry' in data:
            coords = [{'lat': lat, 'lon': lon} for lon, lat in data['geometry'].coords]
        else:
            # Straight road, just use the two endpoints
            coords = [
                {'lat': u_data['y'], 'lon': u_data['x']},
                {'lat': v_data['y'], 'lon': v_data['x']}
            ]

        # Safely extract lanes
        lanes = data.get('lanes', 1)
        if isinstance(lanes, list):
            lanes = int(lanes[0])
        else:
            lanes = int(lanes)

        # Safely extract highway type
        highway = data.get('highway', 'unknown')
        if isinstance(highway, list):
            highway = highway[0]

        edges.append({
            'coords': coords,
            'id': f"{u}-{v}",
            'lanes': lanes,
            'name': data.get('name', 'Unknown'),
            'highway': highway,
            'speed_kph': data.get('speed_kph', 30),
            'length': data.get('length', 0),
            'oneway': data.get('oneway', False),
            'road_score': data.get('road_score', 1.0)
        })

    return {'edges': edges}


def add_road(G, data):
    u = data['start_node']
    v = data['end_node']
    G.add_edge(u, v)
    return G


def remove_road(G, data):
    u = data['start_node']
    v = data['end_node']
    if G.has_edge(u, v):
        G.remove_edge(u, v)
    return G
