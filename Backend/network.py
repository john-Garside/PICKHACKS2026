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
    """
    Matches traffic segments from GeoJSON to the OSMnx graph edges.
    """
    with open(geojson_path, 'r') as f:
        traffic_data = json.load(f)

    print(f"Matching traffic data from {geojson_path}...")

    # Iterate through each feature in the GeoJSON
    for feature in traffic_data['features']:
        # Skip the first metadata feature (it has no geometry)
        if feature['geometry'] is None:
            continue
            
        props = feature['properties']
        
        # Extract the metrics we want
        # Note: We take the first result in segmentTimeResults (the 'Typical' data)
        results = props.get('segmentTimeResults', [{}])[0]
        avg_speed = results.get('harmonicAverageSpeed')
        sample_size = results.get('sampleSize', 0)
        
        # Get the geometry (the line on the map)
        geom = shape(feature['geometry'])
        
        # Find the midpoint of this traffic segment to help us locate it in our graph
        midpoint = geom.interpolate(0.5, normalized=True)
        
        # Use OSMnx to find the nearest edge in our graph to this traffic segment
        # This is the "Magic" that connects the two datasets
        try:
            u, v, key = ox.nearest_edges(G, midpoint.x, midpoint.y)
            
            # Store the traffic data directly on the graph edge
            G[u][v][key]['traffic_speed'] = avg_speed
            G[u][v][key]['traffic_volume'] = sample_size
            
        except Exception as e:
            continue

    print("Traffic data successfully merged into the road network.")
    return G