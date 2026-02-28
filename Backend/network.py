import json
from shapely.geometry import LineString, shape
import osmnx as ox
import networkx as nx

def save_rolla_boundary():
    # Get the exact boundary OSMnx uses for Rolla
    boundary = ox.geocode_to_gdf("Rolla, Missouri, USA")
    boundary.to_file("rolla_boundary.geojson", driver="GeoJSON")
    print("Saved rolla_boundary.geojson")


def load_network():
    G = ox.graph_from_place("Rolla, Missouri, USA", network_type="drive")

    # Add speed and travel time data from OSMnx
    G = ox.add_edge_speeds(G)
    G = ox.add_edge_travel_times(G)

    # Calculate and store road score on every edge
    geojson_file = 'jobs_8773253_results_Rolla_Full.geojson'
    G = apply_traffic_data(G, geojson_file)

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

def apply_traffic_data(G, geojson_path):
    with open(geojson_path, 'r') as f:
        traffic_data = json.load(f)

    # LITE MODE: Only take the first 200 segments for testing
    features = traffic_data['features'][:200] 
    
    print(f"LITE MODE: Matching 200 segments (skipping {len(traffic_data['features'])-200})...")

    for feature in features:
        if feature['geometry'] is None: continue
        
        props = feature['properties']
        results = props.get('segmentTimeResults', [{}])[0]
        
        # Get coordinates
        geom = shape(feature['geometry'])
        midpoint = geom.interpolate(0.5, normalized=True)
        
        try:
            # Find nearest edge
            u, v, key = ox.nearest_edges(G, midpoint.x, midpoint.y)
            G[u][v][key]['traffic_speed'] = results.get('harmonicAverageSpeed')
            G[u][v][key]['traffic_volume'] = results.get('sampleSize', 0)
        except:
            continue

    print("Lite Traffic Data Loaded!")
    return G