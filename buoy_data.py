"""
NDBC buoy data fetching and parsing.
Handles spectral data (.data_spec, .swdir) and bulk parameters (.txt).
"""

import re
import logging
import math
import numpy as np
import requests
from datetime import datetime, timezone, timedelta

log = logging.getLogger(__name__)

BASE_URL = "https://www.ndbc.noaa.gov/data/realtime2"

STATION_NAMES = {
    "46026": "San Francisco, CA",
    "46025": "Santa Monica Basin, CA",
    "46028": "Cape San Martin, CA",
    "46059": "West California, CA",
    "46232": "Point Loma South, CA",
    "46047": "Tanner Bank, CA",
    "51001": "Northwest Hawaii",
    "51004": "Southeast Hawaii",
    "44025": "New Jersey, NJ",
    "44008": "Nantucket, MA",
    "41047": "NE Caribbean",
    "41048": "West Hatteras, NC",
    "46029": "Columbia River Bar, OR",
    "46050": "Stonewall Banks, OR",
}


def _fetch_text(url, timeout=30):
    resp = requests.get(url, timeout=timeout, headers={"User-Agent": "BuoyDataApp/1.0"})
    resp.raise_for_status()
    return resp.text


def _parse_spec_header_freqs(content):
    """Extract frequencies (Hz) from NDBC spectral file header."""
    for line in content.split("\n"):
        if line.startswith("#") and "(" in line:
            freqs = re.findall(r"\((\d+\.\d+)\)", line)
            if freqs:
                return np.array([float(f) for f in freqs])
    return None


def _parse_ndbc_timestamp(tokens):
    """
    Parse date/time from the first tokens of an NDBC data line.
    Returns (datetime_utc, index_of_next_field).
    Handles both 5-field (YY MM DD hh mm) and 10-field (repeated date) formats.
    """
    year = int(tokens[0])
    if year < 100:
        year += 2000 if year < 70 else 1900
    month = int(tokens[1])
    day = int(tokens[2])
    hour = int(tokens[3])
    minute = int(tokens[4])

    # Newer NDBC files repeat the full date (YYYY MM DD hh mm) after the 2-digit block
    next_idx = 5
    if len(tokens) > 9:
        try:
            second_year = int(tokens[5])
            if second_year > 1990:  # looks like a 4-digit year
                next_idx = 10
        except (ValueError, IndexError):
            pass

    ts = datetime(year, month, day, hour, minute, tzinfo=timezone.utc)
    return ts, next_idx


def _parse_spectral_records(content, n_freqs):
    """
    Parse an NDBC spectral data file (.data_spec or .swdir).
    Returns list of {timestamp, sep_freq, values}.
    """
    records = []
    for raw_line in content.split("\n"):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        # Strip any interleaved frequency values in parentheses
        line_clean = re.sub(r"\s*\([^)]+\)", "", line)
        tokens = line_clean.split()
        if len(tokens) < 7:
            continue

        try:
            ts, offset = _parse_ndbc_timestamp(tokens)
        except (ValueError, IndexError):
            continue

        try:
            sep_freq = float(tokens[offset])
            values_start = offset + 1
        except (ValueError, IndexError):
            continue

        values = []
        for i in range(values_start, values_start + n_freqs):
            if i >= len(tokens):
                values.append(np.nan)
                continue
            try:
                v = float(tokens[i])
                # NDBC missing value codes
                if v in (999.0, 9999.0, 99.0, 999.00, 9999.00):
                    v = np.nan
            except ValueError:
                v = np.nan
            values.append(v)

        while len(values) < n_freqs:
            values.append(np.nan)

        records.append({
            "timestamp": ts,
            "sep_freq": sep_freq,
            "values": np.array(values[:n_freqs], dtype=float),
        })

    return records


def fetch_spectral_data(station_id):
    """
    Fetch and merge spectral energy (.data_spec) and direction (.swdir) data.

    Returns dict:
        frequencies: np.array of frequency bins (Hz)
        records: list of {timestamp, sep_freq, energy (m^2/Hz), direction (deg)}
    or None if unavailable.
    """
    spec_url = f"{BASE_URL}/{station_id}.data_spec"
    swdir_url = f"{BASE_URL}/{station_id}.swdir"

    try:
        spec_content = _fetch_text(spec_url)
    except Exception as e:
        log.warning("Cannot fetch spectral data for %s: %s", station_id, e)
        return None

    frequencies = _parse_spec_header_freqs(spec_content)
    if frequencies is None or len(frequencies) < 5:
        log.warning("No valid frequencies in spectral header for %s", station_id)
        return None

    n = len(frequencies)
    spec_records = _parse_spectral_records(spec_content, n)
    if not spec_records:
        return None

    # Directional data is optional
    dir_by_ts = {}
    try:
        swdir_content = _fetch_text(swdir_url)
        for r in _parse_spectral_records(swdir_content, n):
            dir_by_ts[r["timestamp"]] = r["values"]
    except Exception as e:
        log.warning("Cannot fetch swdir for %s: %s", station_id, e)

    merged = []
    for r in spec_records:
        merged.append({
            "timestamp": r["timestamp"],
            "sep_freq": r["sep_freq"],
            "energy": r["values"],
            "direction": dir_by_ts.get(r["timestamp"], np.full(n, np.nan)),
        })

    merged.sort(key=lambda x: x["timestamp"])
    return {"frequencies": frequencies, "records": merged}


def fetch_bulk_data(station_id):
    """
    Fetch standard meteorological data (.txt file).

    Returns list of records (sorted oldest→newest):
        timestamp, wvht_m, dpd_s, apd_s, mwd_deg,
        wspd_ms, wdir_deg, gst_ms, atmp_c, wtmp_c, pres_hpa
    or None if unavailable.
    """
    url = f"{BASE_URL}/{station_id}.txt"
    try:
        content = _fetch_text(url)
    except Exception as e:
        log.warning("Cannot fetch bulk data for %s: %s", station_id, e)
        return None

    lines = content.strip().split("\n")
    header_line = None
    data_lines = []
    for line in lines:
        if line.startswith("#"):
            stripped = line.lstrip("#").strip()
            if stripped and not stripped.startswith("yr") and not stripped.startswith("unit"):
                # First real header
                if header_line is None:
                    header_line = stripped
        else:
            data_lines.append(line)

    if header_line is None:
        return None

    col_names = header_line.split()

    def _get(row_dict, *keys):
        for k in keys:
            v = row_dict.get(k)
            if v and v not in ("MM", "999", "9999", "99", "999.0", "9999.0"):
                try:
                    return float(v)
                except ValueError:
                    pass
        return math.nan

    records = []
    for line in data_lines:
        tokens = line.split()
        if len(tokens) < len(col_names):
            continue
        row = dict(zip(col_names, tokens))

        try:
            ts, _ = _parse_ndbc_timestamp(tokens)
        except Exception:
            continue

        records.append({
            "timestamp": ts,
            "wvht_m": _get(row, "WVHT"),
            "dpd_s": _get(row, "DPD"),
            "apd_s": _get(row, "APD"),
            "mwd_deg": _get(row, "MWD"),
            "wspd_ms": _get(row, "WSPD"),
            "wdir_deg": _get(row, "WDIR"),
            "gst_ms": _get(row, "GST"),
            "atmp_c": _get(row, "ATMP"),
            "wtmp_c": _get(row, "WTMP"),
            "pres_hpa": _get(row, "PRES"),
        })

    records.sort(key=lambda x: x["timestamp"])
    return records if records else None
