import osmnx as ox
import networkx as nx

def load_network():
    # Loads a real road network - change the place to your city
    G = ox.graph_from_place("Rolla, Missouri, USA", network_type="drive")
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
        
        edges.append({
            'coords': coords,
            'id': f"{u}-{v}"
        })
    return {'edges': edges}

def add_road(G, data):
    # Frontend should send: {action, start_node, end_node}
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
