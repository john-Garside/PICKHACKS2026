import random

# Global state
vehicles = []
initialized = False
current_vol_bin = -1  # Tracks if we need to re-spawn cars due to density changes

# CONFIGURATION
# How many seconds pass in the simulation for every backend update
SIMULATION_STEP_TIME = 1.0 
# Adjust this to change overall car density (Higher = fewer cars)
VOLUME_DENSITY_FACTOR = 500 

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
        # 1. Get the baseline volume from GeoJSON data
        base_volume = data.get('traffic_volume', 0)
        
        # 2. Apply the hourly multiplier from the CSV
        adjusted_volume = base_volume * volume_multiplier
        
        # 3. Determine how many dots to show on this specific road
        #num_to_spawn = int(adjusted_volume / VOLUME_DENSITY_FACTOR)
        volume = data.get('traffic_volume', 0)
        if volume > 0:
            num_to_spawn = int(volume / 500)
        else:
        # Spawn 1 random car on 10% of roads so the map isn't empty
            num_to_spawn = 1 if random.random() < 0.1 else 0
        # 4. Get the base speed (from GeoJSON or OSM default)
        speed_kph = data.get('traffic_speed') or data.get('speed_kph', 30)
        if isinstance(speed_kph, list): speed_kph = speed_kph[0]
        
        # 5. Create the vehicles
        for _ in range(num_to_spawn):
            vehicles.append({
                "id": vehicle_id,
                "u": u,
                "v": v,
                "key": key,
                "progress": random.random(),  # Start at a random spot on the block
                "speed_kph": float(speed_kph)
            })
            vehicle_id += 1
            
    print(f"Simulation loaded with {len(vehicles)} vehicles for this hour.")

def get_traffic_positions(G, speed_multiplier=1.0, volume_multiplier=1.0):
    """
    Main loop: Updates vehicle positions based on physics and network flow.
    """
    global initialized, current_vol_bin
    
    # If the user moved the slider and the volume changed significantly, re-spawn cars
    vol_bin = round(volume_multiplier, 1)
    if not initialized or vol_bin != current_vol_bin:
        initialize_vehicles(G, volume_multiplier)
        initialized = True
        current_vol_bin = vol_bin
    
    positions = []
    
    for vehicle in vehicles:
        # 1. Get current road segment details
        edge_data = G[vehicle["u"]][vehicle["v"]][vehicle["key"]]
        length = edge_data.get('length', 10) # length in meters
        
        # 2. Calculate Actual Speed
        # Base Speed (from GeoJSON) * Hourly Pulse (from CSV)
        actual_speed_kph = vehicle["speed_kph"] * speed_multiplier
        speed_mps = actual_speed_kph / 3.6  # Convert to meters per second
        
        # 3. Calculate Progress (0.0 to 1.0)
        # Distance moved / Total road length
        progress_increment = (speed_mps * SIMULATION_STEP_TIME) / length
        vehicle["progress"] += progress_increment
        
        # 4. Handle reaching the end of a road
        if vehicle["progress"] >= 1:
            vehicle["progress"] = 0
            
            # Find the next connected roads (out_edges)
            current_node = vehicle["v"]
            next_options = list(G.out_edges(current_node, keys=True))
            
            if next_options:
                # Move to a connected street (Realistic driving)
                new_u, new_v, new_key = random.choice(next_options)
                vehicle["u"], vehicle["v"], vehicle["key"] = new_u, new_v, new_key
                
                # Update the vehicle's base speed for the new road
                new_data = G[new_u][new_v][new_key]
                new_speed = new_data.get('traffic_speed') or new_data.get('speed_kph', 30)
                if isinstance(new_speed, list): new_speed = new_speed[0]
                vehicle["speed_kph"] = float(new_speed)
            else:
                # Dead end? Reset to a random road in the city
                all_edges = list(G.edges(keys=True))
                vehicle["u"], vehicle["v"], vehicle["key"] = random.choice(all_edges)

        # 5. Coordinate Calculation (Linear Interpolation)
        u_node = G.nodes[vehicle["u"]]
        v_node = G.nodes[vehicle["v"]]
        
        p = vehicle["progress"]
        lat = u_node['y'] + p * (v_node['y'] - u_node['y'])
        lon = u_node['x'] + p * (v_node['x'] - u_node['x'])
        
        positions.append({
            "id": vehicle["id"],
            "lat": lat,
            "lon": lon
        })
    
    return positions