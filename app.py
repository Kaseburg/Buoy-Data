"""
Flask backend for buoy swell data visualization.
Serves the UI and provides API endpoints that fetch + process NDBC data.
"""

import math
import logging

from flask import Flask, jsonify, render_template, request

import buoy_data
from swell_tracker import SwellTracker

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)

app = Flask(__name__)

# Curated list shown in the station selector
STATIONS = [
    {"id": "46026", "name": "San Francisco, CA"},
    {"id": "46028", "name": "Cape San Martin, CA"},
    {"id": "46059", "name": "West California, CA"},
    {"id": "46025", "name": "Santa Monica Basin, CA"},
    {"id": "46232", "name": "Point Loma South, CA"},
    {"id": "46047", "name": "Tanner Bank, CA"},
    {"id": "46029", "name": "Columbia River Bar, OR"},
    {"id": "46050", "name": "Stonewall Banks, OR"},
    {"id": "51001", "name": "Northwest Hawaii"},
    {"id": "51004", "name": "Southeast Hawaii"},
    {"id": "44025", "name": "New Jersey, NJ"},
    {"id": "44008", "name": "Nantucket, MA"},
    {"id": "41047", "name": "NE Caribbean"},
]


# ---------------------------------------------------------------------------
# Unit helpers
# ---------------------------------------------------------------------------

def _m_to_ft(v):
    return round(v * 3.28084, 1) if math.isfinite(v) else None


def _ms_to_mph(v):
    return round(v * 2.23694, 1) if math.isfinite(v) else None


def _c_to_f(v):
    return round(v * 9.0 / 5.0 + 32.0, 1) if math.isfinite(v) else None


def _hpa_to_inhg(v):
    return round(v * 0.02953, 2) if math.isfinite(v) else None


def _clean(v):
    """Return None for NaN/inf floats so JSON serialises cleanly."""
    if isinstance(v, float) and not math.isfinite(v):
        return None
    return v


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html", stations=STATIONS)


@app.route("/api/stations")
def api_stations():
    return jsonify(STATIONS)


@app.route("/api/swells")
def api_swells():
    station_id = request.args.get("station", "46026").strip()
    hours = min(int(request.args.get("hours", 72)), 240)

    tracker = SwellTracker()

    # --- Try spectral data first ---
    spec = buoy_data.fetch_spectral_data(station_id)
    if spec and spec.get("records"):
        log.info("Spectral data: %d records for %s", len(spec["records"]), station_id)
        swells = tracker.process_spectral_series(
            spec["frequencies"], spec["records"], hours=hours
        )
    else:
        log.info("Falling back to bulk data for %s", station_id)
        bulk = buoy_data.fetch_bulk_data(station_id)
        if not bulk:
            return jsonify({"error": f"No data available for station {station_id}"}), 404
        swells = tracker.process_bulk_series(bulk, hours=hours)

    # --- Latest conditions from bulk data ---
    latest = None
    bulk = buoy_data.fetch_bulk_data(station_id)
    if bulk:
        r = bulk[-1]
        latest = {
            "timestamp": r["timestamp"].isoformat(),
            "wvht_ft": _clean(_m_to_ft(r["wvht_m"])),
            "dominant_period_s": _clean(r["dpd_s"]),
            "dominant_dir_deg": _clean(r["mwd_deg"]),
            "water_temp_f": _clean(_c_to_f(r["wtmp_c"])),
            "air_temp_f": _clean(_c_to_f(r["atmp_c"])),
            "wind_speed_mph": _clean(_ms_to_mph(r["wspd_ms"])),
            "wind_dir_deg": _clean(r["wdir_deg"]),
            "wind_gust_mph": _clean(_ms_to_mph(r["gst_ms"])),
            "pressure_inhg": _clean(_hpa_to_inhg(r["pres_hpa"])),
        }

    station_name = next(
        (s["name"] for s in STATIONS if s["id"] == station_id), station_id
    )

    return jsonify({
        "station": station_id,
        "station_name": station_name,
        "swells": swells,
        "latest": latest,
    })


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
