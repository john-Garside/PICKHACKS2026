#Flask Backend 
#test liam

from flask import Flask, jsonify, request
from flask_cors import CORS
from network import load_network, add_road, remove_road, network_to_json
from traffic_simulation import get_traffic_positions

app = Flask(__name__)
CORS(app)

# Load the road network once when the server starts
G = load_network()

@app.route('/roads', methods=['GET'])
def get_roads():
    return jsonify(network_to_json(G))

@app.route('/edit-road', methods=['POST'])
def edit_road():
    global G
    data = request.get_json()
    action = data.get('action')  # 'add' or 'remove'
    
    if action == 'add':
        G = add_road(G, data)
    elif action == 'remove':
        G = remove_road(G, data)
    else:
        return jsonify({'error': 'Invalid action'}), 400
    
    return jsonify({'status': 'success'})

@app.route('/simulate', methods=['GET'])
def simulate():
    positions = get_traffic_positions(G)
    return jsonify(positions)

if __name__ == '__main__':
    app.run(debug=True)
