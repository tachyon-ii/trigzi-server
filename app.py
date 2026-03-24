from flask import Flask, jsonify, request
from core import data_manager
import time
import os

app = Flask(__name__)

MIN_GTIN_LEN = 8
MAX_GTIN_LEN = 14

UNMATCHED_GTIN = 'unmatched_gtin.txt'
UNMATCHED_INGREDIENT = 'unmatched_ingredient.txt'

@app.route('/api/v1/product/<gtin>', methods=['GET'])
def get_product(gtin):
    print(f"[{time.strftime('%H:%M:%S')}] Received scan for GTIN: {gtin}")
    
    if not gtin.isdigit() or not (MIN_GTIN_LEN <= len(gtin) <= MAX_GTIN_LEN):
        print(f"  [!] Rejected: Invalid format.")
        return jsonify({"error": "Invalid barcode."}), 400

    # L1 Cache or Upstream Fetch
    product_data = data_manager.get_product(gtin)
    
    if not product_data:
        return jsonify({"error": "Product not found."}), 404

    return jsonify(product_data), 200

# --- TELEMETRY ROUTES ---

def _log_telemetry(filename, log_type):
    """Private helper to handle the file writing for all telemetry routes."""
    data = request.get_json()
    if not data or 'term' not in data:
        return jsonify({"error": "Invalid payload."}), 400
        
    term = data.get('term')
    source_meal = data.get('source_meal', 'Unknown')
    timestamp = data.get('timestamp', time.strftime('%Y-%m-%dT%H:%M:%SZ'))
    
    try:
        with open(filename, 'a', encoding='utf-8') as f:
            f.write(f"{timestamp} | {term} | {source_meal}\n")
        print(f"[{time.strftime('%H:%M:%S')}] Logged unmatched {log_type}: {term}")
        return jsonify({"status": "logged"}), 200
    except Exception as e:
        print(f"Failed to write telemetry: {e}")
        return jsonify({"error": "Internal server error."}), 500


@app.route('/api/v1/telemetry/unmatched/gtin', methods=['POST'])
def log_unmatched_gtin():
    return _log_telemetry(UNMATCHED_GTIN, "GTIN")


@app.route('/api/v1/telemetry/unmatched/ingredient', methods=['POST'])
def log_unmatched_ingredient():
    return _log_telemetry(UNMATCHED_INGREDIENT, "ingredient")

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
