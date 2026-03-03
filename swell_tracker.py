"""
Robust swell tracker that identifies and tracks individual swell systems
across timesteps using spectral partitioning and data association.

Key principles:
- Treat the buoy record as a spectrum, not a single number
- Swells are clusters in (Period, Direction, Energy) space
- Use cost-function matching + Hungarian assignment to keep IDs stable
- Grace period prevents flicker from noisy or missing peaks
"""

import math
import logging
from datetime import timedelta

import numpy as np
from scipy.optimize import linear_sum_assignment
from scipy.signal import find_peaks

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _circular_diff(a, b):
    """Smallest angular difference between two compass bearings (0–360)."""
    diff = abs(float(a) - float(b)) % 360.0
    return min(diff, 360.0 - diff)


def _compass(deg):
    """Convert degrees to 16-point compass label."""
    dirs = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
            "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    idx = round(deg / 22.5) % 16
    return dirs[idx]


def _smooth(arr, width=5):
    """Simple box-car smoothing of a 1-D array."""
    kernel = np.ones(width) / width
    padded = np.pad(arr, width // 2, mode="edge")
    return np.convolve(padded, kernel, mode="valid")[: len(arr)]


# ---------------------------------------------------------------------------
# Spectral peak detection
# ---------------------------------------------------------------------------

def find_spectral_peaks(frequencies, energy, directions, sep_freq=None):
    """
    Detect individual swell/windsea components from a 1-D energy spectrum.

    Parameters
    ----------
    frequencies : np.ndarray   frequency bins (Hz)
    energy      : np.ndarray   energy density (m^2/Hz)
    directions  : np.ndarray   mean wave direction at each bin (deg), may contain NaN
    sep_freq    : float        NDBC separation frequency between swell & windsea

    Returns
    -------
    list of dicts:
        period      (s)      – centroid period of the cluster
        direction   (deg)    – mean direction at peak, or None
        height_ft   (ft)     – partition significant wave height
        m0          (m^2)    – zeroth moment of cluster
        is_windsea  (bool)   – True if classified as local wind sea
        period_band (tuple)  – (Tp_lo, Tp_hi) bounding the cluster
    """
    # Work on a safe copy, replace NaN/negative with 0
    e = np.where(np.isfinite(energy) & (energy > 0), energy, 0.0)

    # Restrict to physically meaningful range: 0.03–0.40 Hz (2.5 s – 33 s)
    mask = (frequencies >= 0.030) & (frequencies <= 0.400)
    f = frequencies[mask]
    e = e[mask]
    d = directions[mask] if (directions is not None and len(directions) == len(frequencies)) else None

    if len(f) < 4 or e.max() < 1e-6:
        return []

    # Smooth spectrum before peak-finding
    e_smooth = _smooth(e, width=5)

    # Frequency bin widths for integration
    df = np.gradient(f)

    peak_idxs, props = find_peaks(
        e_smooth,
        distance=3,
        prominence=e_smooth.max() * 0.04,
        height=e_smooth.max() * 0.03,
    )

    candidates = []
    for idx in peak_idxs:
        f_peak = f[idx]
        period_peak = 1.0 / f_peak

        if period_peak < 2.5:
            continue

        # ---- Integration band: ±3 bins around peak ----
        lo = max(0, idx - 3)
        hi = min(len(f) - 1, idx + 3)

        e_band = e[lo : hi + 1]
        df_band = df[lo : hi + 1]
        m0 = float(np.sum(e_band * df_band))
        if m0 <= 0:
            continue

        # Centroid period of band (energy-weighted 1/f average)
        periods_band = 1.0 / f[lo : hi + 1]
        period_centroid = float(np.sum(e_band * periods_band * df_band) / m0)

        period_lo = 1.0 / f[min(hi, len(f) - 1)]
        period_hi = 1.0 / f[max(lo, 0)]

        # Significant wave height contribution from this band
        hs_m = 4.0 * math.sqrt(m0)
        hs_ft = hs_m * 3.28084

        # Direction at peak (if available and valid)
        direction = None
        if d is not None and np.isfinite(d[idx]):
            direction = float(d[idx]) % 360.0

        # Wind-sea classification:
        # – freq >= sep_freq (NDBC definition) OR period < 8 s
        is_windsea = False
        if sep_freq is not None and f_peak >= sep_freq:
            is_windsea = True
        elif period_centroid < 8.0:
            is_windsea = True

        candidates.append({
            "period": period_centroid,
            "direction": direction,
            "height_ft": hs_ft,
            "m0": m0,
            "is_windsea": is_windsea,
            "period_band": (period_lo, period_hi),
        })

    return candidates


# ---------------------------------------------------------------------------
# Swell tracker
# ---------------------------------------------------------------------------

SWELL_COLORS = [
    "#2196F3",  # Blue
    "#FF5722",  # Deep Orange
    "#4CAF50",  # Green
    "#9C27B0",  # Purple
    "#FF9800",  # Amber
    "#00BCD4",  # Cyan
    "#F44336",  # Red
    "#8BC34A",  # Light Green
    "#E91E63",  # Pink
    "#607D8B",  # Blue-Grey
    "#FFEB3B",  # Yellow
    "#795548",  # Brown
]


class SwellTracker:
    """
    Tracks individual swell systems across timesteps.

    Usage:
        tracker = SwellTracker()
        histories = tracker.process_spectral_series(frequencies, records, hours=72)
        # or
        histories = tracker.process_bulk_series(bulk_records, hours=72)
    """

    # Gate thresholds for matching candidates → existing swells
    _TP_GATE_LONG = 2.5    # seconds, for period > 12 s
    _TP_GATE_SHORT = 1.5   # seconds, for period ≤ 12 s
    _DP_GATE = 25.0        # degrees
    _GRACE = 5             # timesteps before retiring a swell

    def __init__(self):
        self._active = {}       # swell_id -> state dict
        self._histories = {}    # swell_id -> history dict
        self._color_idx = 0
        self._date_counts = {}  # date_str -> counter

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def process_spectral_series(self, frequencies, records, hours=72):
        """
        Process a full spectral time series.

        Parameters
        ----------
        frequencies : np.ndarray
        records     : list of {timestamp, sep_freq, energy, direction}
        hours       : int – how many hours of history to return

        Returns
        -------
        dict  swell_id -> {color, label, is_windsea, data: [...]}
        """
        if not records:
            return {}

        latest_ts = max(r["timestamp"] for r in records)
        cutoff = latest_ts - timedelta(hours=hours)
        window = [r for r in records if r["timestamp"] >= cutoff]

        for r in window:
            energy = r.get("energy", np.zeros(len(frequencies)))
            direction = r.get("direction", None)
            sep_freq = r.get("sep_freq", None)

            energy = np.where(np.isfinite(energy), energy, 0.0)
            candidates = find_spectral_peaks(frequencies, energy, direction, sep_freq)
            self._update(r["timestamp"], candidates)

        return self._compile()

    def process_bulk_series(self, records, hours=72):
        """
        Fallback: process bulk parameter records (Hs, DPD, MWD) when no
        spectral data is available. Tracks the dominant period/direction.
        """
        if not records:
            return {}

        latest_ts = max(r["timestamp"] for r in records)
        cutoff = latest_ts - timedelta(hours=hours)
        window = [r for r in records if r["timestamp"] >= cutoff]

        for r in window:
            wvht = r.get("wvht_m", math.nan)
            dpd = r.get("dpd_s", math.nan)
            mwd = r.get("mwd_deg", math.nan)

            if not math.isfinite(wvht) or not math.isfinite(dpd) or wvht <= 0:
                self._update(r["timestamp"], [])
                continue

            hs_ft = wvht * 3.28084
            direction = mwd if math.isfinite(mwd) else None

            candidates = [{
                "period": dpd,
                "direction": direction,
                "height_ft": hs_ft,
                "m0": (wvht / 4.0) ** 2,
                "is_windsea": dpd < 8.0,
                "period_band": (dpd - 1.0, dpd + 1.0),
            }]
            self._update(r["timestamp"], candidates)

        return self._compile()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _update(self, timestamp, candidates):
        """Associate candidates at this timestep with existing swell IDs."""
        active_ids = list(self._active.keys())

        # ---- Both empty ----
        if not active_ids and not candidates:
            return

        # ---- No existing swells – all candidates are new ----
        if not active_ids:
            for c in candidates:
                self._spawn(timestamp, c)
            return

        # ---- No candidates – increment missing count ----
        if not candidates:
            for sid in list(self._active.keys()):
                self._active[sid]["missing"] += 1
                if self._active[sid]["missing"] >= self._GRACE:
                    del self._active[sid]
            return

        # ---- Build cost matrix and solve assignment ----
        n_c = len(candidates)
        n_s = len(active_ids)
        INF = 1e9

        cost = np.full((n_c, n_s), INF)
        for i, c in enumerate(candidates):
            for j, sid in enumerate(active_ids):
                cost[i, j] = self._cost(c, self._active[sid])

        row_ind, col_ind = linear_sum_assignment(np.minimum(cost, 1e6))

        matched_c = set()
        matched_s = set()

        for ri, ci in zip(row_ind, col_ind):
            if cost[ri, ci] < 1e8:
                sid = active_ids[ci]
                self._record(timestamp, sid, candidates[ri])
                matched_c.add(ri)
                matched_s.add(ci)

        # Unmatched swells – increment grace counter
        for j, sid in enumerate(active_ids):
            if j not in matched_s:
                self._active[sid]["missing"] += 1
                if self._active[sid]["missing"] >= self._GRACE:
                    del self._active[sid]

        # Unmatched candidates – spawn new swell
        for i, c in enumerate(candidates):
            if i not in matched_c:
                self._spawn(timestamp, c)

    def _cost(self, candidate, state):
        """
        Compute matching cost between a candidate peak and an active swell.
        Returns a value in [0, 2] for valid matches, or 1e9 if gated out.
        """
        tp_c = candidate["period"]
        tp_s = state["period"]
        tp_gate = self._TP_GATE_LONG if tp_s > 12.0 else self._TP_GATE_SHORT

        dtp = abs(tp_c - tp_s)
        if dtp > tp_gate:
            return 1e9

        dp_cost = 0.0
        dp_s = state.get("direction")
        dp_c = candidate.get("direction")
        if dp_s is not None and dp_c is not None:
            ddp = _circular_diff(dp_c, dp_s)
            if ddp > self._DP_GATE:
                return 1e9
            dp_cost = ddp / self._DP_GATE

        tp_cost = dtp / tp_gate
        return tp_cost + dp_cost

    def _spawn(self, timestamp, candidate):
        """Create a new swell entry for an unmatched candidate."""
        date_str = timestamp.strftime("%Y%m%d")
        count = self._date_counts.get(date_str, 0)
        self._date_counts[date_str] = count + 1
        letter = chr(ord("A") + (count % 26))

        tp = round(candidate["period"])
        dp = candidate["direction"]
        dp_str = f"{round(dp / 10) * 10:03d}" if dp is not None else "UNK"

        sid = f"SW_{tp:02d}s_{dp_str}_{date_str}_{letter}"
        color = SWELL_COLORS[self._color_idx % len(SWELL_COLORS)]
        self._color_idx += 1

        # Human-readable label
        if candidate["is_windsea"]:
            label = "Wind Sea"
        elif dp is not None:
            label = f"{_compass(dp)} {tp}s"
        else:
            label = f"Swell {tp}s"

        self._active[sid] = {
            "period": candidate["period"],
            "direction": candidate["direction"],
            "height_ft": candidate["height_ft"],
            "is_windsea": candidate["is_windsea"],
            "missing": 0,
            "color": color,
            "label": label,
        }

        self._histories[sid] = {
            "color": color,
            "label": label,
            "is_windsea": candidate["is_windsea"],
            "data": [self._datapoint(timestamp, candidate)],
        }

    def _record(self, timestamp, sid, candidate):
        """Append a data point to an existing swell's history."""
        state = self._active[sid]
        state["period"] = candidate["period"]
        if candidate["direction"] is not None:
            state["direction"] = candidate["direction"]
        state["height_ft"] = candidate["height_ft"]
        state["missing"] = 0
        self._histories[sid]["data"].append(self._datapoint(timestamp, candidate))

    @staticmethod
    def _datapoint(timestamp, candidate):
        dp = candidate["direction"]
        return {
            "timestamp": timestamp.isoformat(),
            "height_ft": round(candidate["height_ft"], 2),
            "period_s": round(candidate["period"], 1),
            "direction_deg": round(dp, 0) if dp is not None else None,
        }

    def _compile(self):
        """Return swell histories that have at least 2 data points."""
        return {
            sid: hist
            for sid, hist in self._histories.items()
            if len(hist["data"]) >= 2
        }
