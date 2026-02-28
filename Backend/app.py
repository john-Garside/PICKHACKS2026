#Flask Backend 
#test ignoring branch
import pandas as pd
from flask import Flask, jsonify, request
from flask_cors import CORS
from network import load_network, add_road, remove_road, network_to_json
from traffic_simulation import get_traffic_positions

app = Flask(__name__)
CORS(app)

# Load and prepare the Hourly "Pulse" data
df = pd.read_csv('RollaReport.csv')
df['Time'] = pd.to_datetime(df['Time'])
df['hour'] = df['Time'].dt.hour

hourly_speeds = df.groupby('hour')['Speed [kmh]'].mean()
overall_avg_speed = hourly_speeds.mean()

# Demand increases when speeds drop
demand_multipliers = {
    hour: overall_avg_speed / hourly_speeds[hour]
    for hour in hourly_speeds.index
}

# Speed scales relative to overall average
speed_multipliers = {
    hour: hourly_speeds[hour] / overall_avg_speed
    for hour in hourly_speeds.index
}

# Load the road network once when the server starts
G = load_network()

@app.route('/roads', methods=['GET'])
def get_roads():
    return jsonify(network_to_json(G))

@app.route('/edit-road', methods=['POST'])
def edit_road():
    global G
    data = request.get_json()

    if not data:
        return jsonify({'error': 'No data received'}), 400

    action = data.get('action')

    if 'start_node' not in data or 'end_node' not in data:
        return jsonify({'error': 'Missing node data'}), 400

    if action == 'add':
        G = add_road(G, data)
    elif action == 'remove':
        G = remove_road(G, data)
    else:
        return jsonify({'error': 'Invalid action'}), 400

    return jsonify({'status': 'success'})

@app.route('/simulate', methods=['GET'])
def simulate():
    hour = int(request.args.get('hour', 12))

    s_mult = speed_multipliers.get(hour, 1.0)
    v_mult = demand_multipliers.get(hour, 1.0)

    positions = get_traffic_positions(
        G,
        speed_multiplier=s_mult,
        volume_multiplier=v_mult
    )

    return jsonify(positions)

if __name__ == '__main__':
    app.run(debug=True)
