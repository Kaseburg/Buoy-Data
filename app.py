from flask import Flask, jsonify, send_file
from flask_cors import CORS
import requests
from datetime import datetime, timedelta, timezone

app = Flask(__name__)
CORS(app)

BUOYS = [
    # West Coast
    {"id": "46026", "name": "San Francisco",      "state": "CA", "region": "West Coast",    "lat": 37.759,  "lon": -122.833},
    {"id": "46025", "name": "Santa Monica Basin", "state": "CA", "region": "West Coast",    "lat": 33.749,  "lon": -119.053},
    {"id": "46042", "name": "Monterey",            "state": "CA", "region": "West Coast",    "lat": 36.785,  "lon": -122.469},
    {"id": "46047", "name": "Point Arena",         "state": "CA", "region": "West Coast",    "lat": 38.479,  "lon": -123.476},
    {"id": "46028", "name": "Point Conception",    "state": "CA", "region": "West Coast",    "lat": 34.902,  "lon": -121.884},
    {"id": "46219", "name": "Harvest Platform",    "state": "CA", "region": "West Coast",    "lat": 34.447,  "lon": -120.782},
    {"id": "46087", "name": "Neah Bay",            "state": "WA", "region": "West Coast",    "lat": 48.493,  "lon": -124.728},
    {"id": "46089", "name": "Tillamook",           "state": "OR", "region": "West Coast",    "lat": 45.774,  "lon": -125.773},
    {"id": "46029", "name": "Columbia River Bar",  "state": "OR", "region": "West Coast",    "lat": 46.144,  "lon": -124.512},
    # East Coast
    {"id": "44025", "name": "New Jersey",          "state": "NJ", "region": "East Coast",    "lat": 40.251,  "lon": -73.166},
    {"id": "44013", "name": "Boston",              "state": "MA", "region": "East Coast",    "lat": 42.346,  "lon": -70.651},
    {"id": "44017", "name": "Montauk Point",       "state": "NY", "region": "East Coast",    "lat": 40.694,  "lon": -72.046},
    {"id": "44008", "name": "Nantucket",           "state": "MA", "region": "East Coast",    "lat": 40.502,  "lon": -69.247},
    {"id": "41047", "name": "Cape Hatteras",       "state": "NC", "region": "East Coast",    "lat": 27.519,  "lon": -71.494},
    {"id": "41048", "name": "West Bermuda",        "state": "NC", "region": "East Coast",    "lat": 31.980,  "lon": -69.591},
    # Gulf of Mexico
    {"id": "42001", "name": "Gulf East",           "state": "MS", "region": "Gulf of Mexico","lat": 25.888,  "lon": -89.658},
    {"id": "42039", "name": "Gulf West",           "state": "TX", "region": "Gulf of Mexico","lat": 28.789,  "lon": -86.006},
    # Hawaii
    {"id": "51001", "name": "Northwest Hawaii",    "state": "HI", "region": "Hawaii",        "lat": 23.445,  "lon": -162.279},
    {"id": "51004", "name": "Southeast Hawaii",    "state": "HI", "region": "Hawaii",        "lat": 17.525,  "lon": -152.382},
    # Great Lakes
    {"id": "45007", "name": "Lake Michigan South", "state": "WI", "region": "Great Lakes",   "lat": 42.674,  "lon": -87.025},
    {"id": "45012", "name": "Lake Erie",           "state": "OH", "region": "Great Lakes",   "lat": 42.158,  "lon": -81.351},
]


def degrees_to_compass(deg):
    if deg is None:
        return "N/A"
    directions = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
                  "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    idx = round(deg / 22.5) % 16
    return directions[idx]


def parse_ndbc_txt(text):
    """Parse NDBC standard meteorological data, returning records within the last 48 hours."""
    lines = [l for l in text.strip().split("\n") if l.strip()]
    if len(lines) < 3:
        return []

    header = lines[0].lstrip("#").split()
    # lines[1] is units row — skip it
    cutoff = datetime.now(timezone.utc) - timedelta(hours=48)

    records = []
    for line in lines[2:]:
        if line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 5:
            continue
        try:
            dt = datetime(int(parts[0]), int(parts[1]), int(parts[2]),
                          int(parts[3]), int(parts[4]), tzinfo=timezone.utc)
        except ValueError:
            continue
        if dt < cutoff:
            continue

        idx = {h: i for i, h in enumerate(header)}

        def get(name):
            i = idx.get(name)
            if i is None or i >= len(parts) or parts[i] == "MM":
                return None
            try:
                return float(parts[i])
            except ValueError:
                return None

        wvht = get("WVHT")
        dpd  = get("DPD")
        mwd  = get("MWD")
        records.append({
            "timestamp":        dt.isoformat(),
            "wave_height":      wvht,
            "wave_height_ft":   round(wvht * 3.28084, 2) if wvht is not None else None,
            "dominant_period":  dpd,
            "avg_period":       get("APD"),
            "wave_direction":   mwd,
            "wave_dir_label":   degrees_to_compass(mwd),
            "wind_direction":   get("WDIR"),
            "wind_dir_label":   degrees_to_compass(get("WDIR")),
            "wind_speed":       get("WSPD"),
            "wind_speed_kt":    round(get("WSPD") * 1.94384, 1) if get("WSPD") is not None else None,
            "wind_gust":        get("GST"),
            "water_temp_c":     get("WTMP"),
            "water_temp_f":     round(get("WTMP") * 9/5 + 32, 1) if get("WTMP") is not None else None,
            "air_temp_c":       get("ATMP"),
            "air_temp_f":       round(get("ATMP") * 9/5 + 32, 1) if get("ATMP") is not None else None,
            "pressure":         get("PRES"),
        })

    records.sort(key=lambda r: r["timestamp"])
    return records


@app.route("/")
def index():
    return send_file("index.html")


@app.route("/api/buoys")
def api_buoys():
    return jsonify(BUOYS)


@app.route("/api/buoy/<station_id>")
def api_buoy(station_id):
    buoy = next((b for b in BUOYS if b["id"] == station_id), None)
    if not buoy:
        return jsonify({"error": "Buoy not found"}), 404

    url = f"https://www.ndbc.noaa.gov/data/realtime2/{station_id}.txt"
    try:
        resp = requests.get(url, timeout=15,
                            headers={"User-Agent": "NOAA-Buoy-Dashboard/1.0"})
        resp.raise_for_status()
    except requests.HTTPError as e:
        return jsonify({"error": f"NOAA returned {e.response.status_code}"}), 502
    except requests.RequestException as e:
        return jsonify({"error": str(e)}), 502

    records = parse_ndbc_txt(resp.text)
    return jsonify({
        "station": station_id,
        "info":    buoy,
        "latest":  records[-1] if records else None,
        "history": records,
    })


if __name__ == "__main__":
    app.run(debug=True, port=5000)
