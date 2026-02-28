import random

vehicles = []
initialized = False

def initialize_vehicles(G, num_vehicles=500):
    global vehicles
    vehicles = []
    
    edges = list(G.edges())
    
    for i in range(num_vehicles):
        u, v = random.choice(edges)
        vehicles.append({
            "id": i,
            "u": u,
            "v": v,
            "progress": random.random(),  # start somewhere along edge
            "speed": 0.01
        })


def get_traffic_positions(G):
    global initialized
    
    if not initialized:
        initialize_vehicles(G)
        initialized = True
    
    positions = []
    
    for vehicle in vehicles:
        vehicle["progress"] += vehicle["speed"]
        
        if vehicle["progress"] >= 1:
            vehicle["progress"] = 0
            vehicle["u"], vehicle["v"] = random.choice(list(G.edges()))
        
        u = vehicle["u"]
        v = vehicle["v"]
        
        lat1 = G.nodes[u]['y']
        lon1 = G.nodes[u]['x']
        lat2 = G.nodes[v]['y']
        lon2 = G.nodes[v]['x']
        
        # Linear interpolation
        progress = vehicle["progress"]
        lat = lat1 + progress * (lat2 - lat1)
        lon = lon1 + progress * (lon2 - lon1)
        
        positions.append({
            "id": vehicle["id"],
            "lat": lat,
            "lon": lon
        })
    
    return positions