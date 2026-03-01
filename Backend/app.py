# Flask Backend
import pandas as pd
from flask import Flask, jsonify, request
from flask_cors import CORS

from network import load_network, add_road, remove_road, network_to_json
from traffic_simulation import get_traffic_positions, get_road_heat, get_signal_states

app = Flask(__name__)
CORS(app)

# ============================
# Load Hourly Pulse Data
# ============================
df = pd.read_csv('RollaReport.csv')
df['Time'] = pd.to_datetime(df['Time'])
df['hour'] = df['Time'].dt.hour

hourly_speeds = df.groupby('hour')['Speed [kmh]'].mean()
hourly_freeflow = df.groupby('hour')['Free flow speed [kmh]'].mean()
hourly_congestion = df.groupby('hour')['Congestion level [%]'].mean()
peak_congestion = hourly_congestion.max()

demand_multipliers = {
    hour: hourly_congestion[hour] / peak_congestion
    for hour in hourly_congestion.index
}

speed_multipliers = {
    hour: hourly_speeds[hour] / hourly_freeflow[hour]
    for hour in hourly_speeds.index
}
# ============================
# Load Network
# ============================
G = load_network()


# ============================
# ROUTES
# ============================

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


@app.route('/road-heat', methods=['GET'])
def road_heat():
    """
    Advances the simulation one step, then returns:
    {
      "counts": {edge_id: car_count},
      "heat":   {edge_id: 0..1 normalized}
    }
    """
    hour = int(request.args.get('hour', 12))

    s_mult = speed_multipliers.get(hour, 1.0)
    v_mult = demand_multipliers.get(hour, 1.0)

    # ✅ Advance the simulation so cars actually move / exist
    _ = get_traffic_positions(
        G,
        speed_multiplier=s_mult,
        volume_multiplier=v_mult
    )

    # ✅ Now compute heat based on updated vehicle locations
    heat_data = get_road_heat(G)
    return jsonify(heat_data)


@app.route('/signals', methods=['GET'])
def signals():
    """
    Returns current traffic-signal phases at signalized nodes.
    NOTE: Does not advance the sim by itself. Phase updates as /simulate or /road-heat advance the timer.
    """
    return jsonify(get_signal_states(G))


# ============================
# Run Server
# ============================
if __name__ == '__main__':
    app.run(debug=True)