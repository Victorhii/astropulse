from flask import Flask, render_template, send_from_directory, make_response, request, jsonify
import os
import requests
from lunar_probe import get_lunar_conditions, pretty
import urllib.request
from datetime import datetime, timezone
import json


app = Flask(__name__)
app.secret_key = 'V_xjx7190astropulse'

TIMEZONE = "UTC"
@app.route("/api/time")
def get_time_from_api():
    """Fetch current UTC time from timeapi.io and return a Unix millisecond timestamp."""
    url = f"https://timeapi.io/api/time/current/zone?timeZone={TIMEZONE}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})

    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode("utf-8"))

        dt = datetime(
            data["year"],
            data["month"],
            data["day"],
            data["hour"],
            data["minute"],
            data["seconds"],
            data["milliSeconds"] * 1000,
            tzinfo=timezone.utc,
        )

        return jsonify({
            "source": "timeapi.io",
            "timezone": "UTC",
            "timestamp_ms": int(dt.timestamp() * 1000),
        })

    except Exception as error:
        now = datetime.now(timezone.utc)
        return jsonify({
            "source": "pc-fallback",
            "timezone": "UTC",
            "error": str(error),
            "timestamp_ms": int(now.timestamp() * 1000),
        })

# Path to static image folder
TEXTURE_DIR = os.path.join(app.root_path, 'static', 'images')
TEXTURE_PATH = os.path.join(TEXTURE_DIR, 'lunar_surface_8k.jpg')

# Official NASA SVS LRO 8K Global Equirectangular Mosaic URL
NASA_8K_TEXTURE_URL = "https://www.solarsystemscope.com/textures/download/8k_moon.jpg"

@app.route('/')
@app.route('/dashboard')
def dashboard():
    # Pass active_page to highlight the active menu item dynamically if needed
    return render_template('dashboard.html', active_page='dashboard')

@app.route('/comparison')
def comparison():
    return render_template('comparison.html', active_page='comparison')

@app.route('/simulation')
def simulation():
    return render_template('simulation.html', active_page='simulation')

@app.route('/knowledge')
def knowledge():
    return render_template('knowledge.html', active_page='knowledge')

@app.route('/query')
def query():
    return render_template('query.html')

@app.route('/api/lunar-texture-8k')
def get_lunar_texture_8k():
    os.makedirs(TEXTURE_DIR, exist_ok=True)
    
    # Download NASA 8K Texture once if not locally cached
    if not os.path.exists(TEXTURE_PATH):
        print("[Astropulse] Downloading high-resolution 8K NASA Lunar Texture (One-time)...")
        res = requests.get(NASA_8K_TEXTURE_URL, timeout=60)
        if res.status_code == 200:
            with open(TEXTURE_PATH, 'wb') as f:
                f.write(res.content)
            print("[Astropulse] 8K Texture saved successfully!")
        else:
            return "Failed to fetch NASA texture", 500

    # Serve cached local file with 30-day browser cache headers
    response = make_response(send_from_directory(TEXTURE_DIR, 'lunar_surface_8k.jpg'))
    response.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
    return response

import struct
import zlib
import numpy as np
from flask import send_file

LAYER_MAP_PATH = os.path.join(TEXTURE_DIR, 'lunar_layer_data.png')
LDEM_4_URLS = [
    "https://pds-geosciences.wustl.edu/lro/lro-l-lola-3-rdr-v1/lrolol_1xxx/data/lola_gdr/cylindrical/img/ldem_4.img",
    "https://imbrium.mit.edu/DATA/LOLA_GDR/CYLINDRICAL/IMG/LDEM_4.IMG",
]


def _encode_rgb_png(rgb: np.ndarray) -> bytes:
    """Encode an (H, W, 3) uint8 numpy array into a valid PNG without extra dependencies."""
    h, w, _ = rgb.shape
    raw = b"".join(b"\x00" + rgb[y].tobytes() for y in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack("!I", len(data))
            + tag
            + data
            + struct.pack("!I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack("!IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b"")


@app.route('/api/lunar-layer-map')
def get_lunar_layer_map():
    """
    One-time build & cache of global equirectangular RGB data map (1440x720, -180..+180 deg):
      R = Elevation   [-9000 m .. +10500 m]
      G = Slope Angle [0 deg   .. 35 deg]
      B = Water Ice Stability Index [0.00 .. 1.00]
    """
    os.makedirs(TEXTURE_DIR, exist_ok=True)
    if not os.path.exists(LAYER_MAP_PATH):
        raw_bytes = None
        for url in LDEM_4_URLS:
            try:
                res = requests.get(url, timeout=60)
                if res.status_code == 200 and len(res.content) == 720 * 1440 * 2:
                    raw_bytes = res.content
                    break
            except Exception:
                continue
        if raw_bytes is None:
            return "Failed to fetch NASA LOLA global DEM", 500

        # LOLA LDEM_4 is 720x1440 int16 (0.5 m scale), stored 0..360E -> roll 180 deg to match 8k_moon.jpg
        dem_m = np.frombuffer(raw_bytes, dtype="<i2").reshape((720, 1440)).astype(np.float32) * 0.5
        dem_m = np.roll(dem_m, 720, axis=1)

        # Channel R: Elevation normalized [-9000 m, +10500 m]
        elev_norm = np.clip((dem_m + 9000.0) / 19500.0, 0.0, 1.0)

        # Channel G: Terrain Slope Angle (deg) on R_MOON = 1737.4 km
        lats = np.linspace(89.875, -89.875, 720, dtype=np.float32)[:, None]
        cos_lat = np.maximum(np.cos(np.radians(lats)), 0.05)
        dy_m = (np.pi / 180.0) * 0.25 * 1737400.0
        dx_m = dy_m * cos_lat
        dz_dy = np.gradient(dem_m, axis=0) / dy_m
        dz_dx = np.gradient(dem_m, axis=1) / dx_m
        slope_deg = np.degrees(np.arctan(np.hypot(dz_dx, dz_dy)))
        slope_norm = np.clip(slope_deg / 35.0, 0.0, 1.0)

        # Channel B: Polar Cold-Trap Water Ice Stability Index [0..1] (Hayne/Vasavada thermal regime)
        poleward_tilt = np.where(lats >= 0.0, -np.degrees(np.arctan(dz_dy)), np.degrees(np.arctan(dz_dy)))
        max_sun_el = np.clip(90.0 - np.abs(lats) + 1.54 - np.maximum(poleward_tilt, 0.0) * 0.85, 0.0, 90.0)
        sin_el = np.sin(np.radians(max_sun_el))
        # Effective subsurface peak temperature proxy (K) & 1 Gyr sublimation threshold (~110 K)
        t_sub_k = 390.0 * np.power(np.maximum(sin_el, 1e-4), 0.25) * (1.0 - 0.18 * np.clip(slope_deg / 30.0, 0.0, 1.0))
        ice_stability = 1.0 / (1.0 + np.exp((t_sub_k - 112.0) / 8.5))
        ice_stability = np.where(np.abs(lats) < 55.0, 0.0, ice_stability)

        rgb = np.stack([
            (elev_norm * 255.0).astype(np.uint8),
            (slope_norm * 255.0).astype(np.uint8),
            (np.clip(ice_stability, 0.0, 1.0) * 255.0).astype(np.uint8),
        ], axis=-1)

        with open(LAYER_MAP_PATH, 'wb') as f:
            f.write(_encode_rgb_png(rgb))

    response = make_response(send_file(LAYER_MAP_PATH, mimetype='image/png'))
    response.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
    return response

import math
from flask import Flask, request, jsonify, render_template

# Weighted criteria totaling 100%
CRITERIA_WEIGHTS = {
    "earth_elevation": 0.10,    # 10%
    "earthset_duration": 0.10,  # 10%
    "solar_elevation": 0.10,    # 10%
    "sunset_duration": 0.15,    # 15%
    "solar_irradiance": 0.15,   # 15%
    "diurnal_temp": 0.15,       # 15%
    "slope_angle": 0.15,        # 15%
    "water_ice": 0.10,          # 10%
}

# Full-mark thresholds (14 Earth days = 1,209,600 seconds for half-lunation window)
FULL_MARK_EARTH_ELEV = 75.0       # >= 75° overhead communication gets 100%
FULL_MARK_SOLAR_ELEV = 60.0       # >= 60° high sun angle gets 100%
FULL_MARK_DURATION_S = 14 * 86400 # >= 14 days continuous window gets 100%
FULL_MARK_IRRADIANCE = 1000.0     # >= 1000 W/m² peak solar power gets 100%
FULL_MARK_SLOPE_MAX = 5.0         # <= 5° flat terrain gets 100%
ZERO_MARK_SLOPE_MIN = 45.0        # >= 45° hazardous cliff gets 0%
TEMP_EXCEED_TOLERANCE = 200.0     # 200 K total exceedance drops score to 0%


def format_iso_utc(date_str: str, time_str: str) -> str:
    """Converts 'DD/MM/YYYY' and 'HH:MM:SS' into 'YYYY-MM-DDTHH:MM:SSZ'."""
    parts = date_str.strip().split('/')
    if len(parts) == 3:
        dd, mm, yyyy = parts[0].zfill(2), parts[1].zfill(2), parts[2]
        return f"{yyyy}-{mm}-{dd}T{time_str.strip()}Z"
    return f"{date_str.strip()}T{time_str.strip()}Z"

def safe_float(val, default: float = 0.0) -> float:
    """Safely converts numbers or numpy scalars to float, falling back if 'N/A' or None."""
    if val is None or isinstance(val, bool):
        return default
    if isinstance(val, str) and val.strip().upper() == "N/A":
        return default
    try:
        num = float(val)
        return default if math.isnan(num) else num
    except (ValueError, TypeError):
        return default


def evaluate_and_rank_sites(raw_sites: list, user_min_k: float, user_max_k: float) -> dict:
    """
    Scores all 4 lunar sites across 8 criteria, computes bar scales,
    assigns crowns to highest scorers per criterion, and ranks overall feasibility.
    """
    sites = []

    for idx, item in enumerate(raw_sites, start=1):
        cond = item["conditions"]
        lat = float(item["lat"])
        lon = float(item["lon"])
        iso_utc = format_iso_utc(item["date"], item["time"])
        display_id = f"({lat:.4f}, {lon:.4f}), {iso_utc}"

        # Extract raw metrics safely (handles 'N/A' when Sun is below horizon or DEM is missing)
        earth_elev = safe_float(cond.get("2_earth_elevation_deg"), -90.0)
        solar_elev = safe_float(cond.get("1_solar_elevation_deg"), -90.0)

        raw_irr = cond.get("3_solar_irradiance_W_m2", 0.0)
        is_irr_na = isinstance(raw_irr, str) and raw_irr.strip().upper() == "N/A"
        irradiance = 0.0 if is_irr_na else max(0.0, safe_float(raw_irr, 0.0))

        slope = max(0.0, min(90.0, safe_float(cond.get("9_slope_deg"), 0.0)))
        water_ice = max(0.0, min(1.0, safe_float(cond.get("10_water_ice", {}).get("stability_index_0to1"), 0.0)))

        # Temperature tuple: (Tmax, Tmin) -> ensure ordered (site_tmin, site_tmax)
        t_pair = cond.get("7b_lunation_Tmax_Tmin_K", (0.0, 0.0))
        if isinstance(t_pair, (list, tuple)) and len(t_pair) >= 2:
            t1, t2 = safe_float(t_pair[0], 0.0), safe_float(t_pair[1], 0.0)
            site_tmin, site_tmax = min(t1, t2), max(t1, t2)
        else:
            site_tmin, site_tmax = 0.0, 0.0

        # --- Earth Event (Handles earthset, earthrise, and >=400-day perpetual states) ---
        earth_ev = cond.get("5_earth_event") or {}
        earth_ev_type = str(earth_ev.get("event") or "").strip().lower()
        earth_note = str(earth_ev.get("note") or "").strip()
        earth_note_lower = earth_note.lower()

        is_earth_never_sets = (earth_ev_type == "" and "never sets" in earth_note_lower)
        is_earth_never_rises = (earth_ev_type == "" and "never rises" in earth_note_lower)
        is_earthset = (earth_ev_type == "earthset")

        if is_earth_never_sets:
            earth_valid = True
            earth_perpetual = True
            earthset_sec = 400.0 * 86400.0
            earth_display = "≥ 400 d (Never sets)"
            s_earthset = 1.0  # Full mark: uninterrupted Earth line-of-sight
        elif is_earthset:
            earth_valid = True
            earth_perpetual = False
            earthset_sec = safe_float(earth_ev.get("seconds"), 0.0)
            earthset_hms = str(earth_ev.get("hms", ""))
            earth_display = f"{(earthset_sec / 86400.0):.2f} d ({earthset_hms})"
            s_earthset = min(1.0, earthset_sec / FULL_MARK_DURATION_S) if earthset_sec > 0 else 0.0
        elif is_earth_never_rises:
            earth_valid = False
            earth_perpetual = False
            earthset_sec = 0.0
            earth_display = "N/A (Never rises / Far-side)"
            s_earthset = 0.0
        else:
            # Standard 'earthrise' blackout window
            earth_valid = False
            earth_perpetual = False
            earthset_sec = 0.0
            earth_display = "N/A (Awaiting Earthrise)"
            s_earthset = 0.0

        # --- Sun Event (Handles sunset, sunrise, and >=400-day perpetual day/night) ---
        sun_ev = cond.get("4_sun_event") or {}
        sun_ev_type = str(sun_ev.get("event") or "").strip().lower()
        sun_note = str(sun_ev.get("note") or "").strip()
        sun_note_lower = sun_note.lower()

        is_sun_stays_up = (sun_ev_type == "" and "stays up" in sun_note_lower)
        is_sun_stays_down = (sun_ev_type == "" and "stays down" in sun_note_lower)
        is_sunset = (sun_ev_type == "sunset")

        if is_sun_stays_up:
            sun_valid = True
            sun_perpetual = True
            sunset_sec = 400.0 * 86400.0
            sun_display = "≥ 400 d (No sunset)"
            s_sunset = 1.0  # Full mark: continuous solar illumination
        elif is_sunset:
            sun_valid = True
            sun_perpetual = False
            sunset_sec = safe_float(sun_ev.get("seconds"), 0.0)
            sunset_hms = str(sun_ev.get("hms", ""))
            sun_display = f"{(sunset_sec / 86400.0):.2f} d ({sunset_hms})"
            s_sunset = min(1.0, sunset_sec / FULL_MARK_DURATION_S) if sunset_sec > 0 else 0.0
        elif is_sun_stays_down:
            sun_valid = False
            sun_perpetual = False
            sunset_sec = 0.0
            sun_display = "N/A (No sunrise ≥ 400 d)"
            s_sunset = 0.0
        else:
            # Standard 'sunrise' lunar night window
            sun_valid = False
            sun_perpetual = False
            sunset_sec = 0.0
            sun_display = "N/A (Awaiting Sunrise)"
            s_sunset = 0.0

        # --- Normalized Criteria Scores (0.0 to 1.0) ---
        # 1. Earth Elevation Angle (<= 0° -> 0, >= 75° -> 1.0)
        s_earth_elev = 0.0 if earth_elev <= 0.0 else min(1.0, earth_elev / FULL_MARK_EARTH_ELEV)

        # 2. Duration before Earthset (N/A -> 0, >= 14d -> 1.0)
        s_earthset = min(1.0, earthset_sec / FULL_MARK_DURATION_S) if (is_earthset and earthset_sec > 0) else 0.0

        # 3. Solar Elevation Angle (<= 0° -> 0, >= 60° -> 1.0)
        s_solar_elev = 0.0 if solar_elev <= 0.0 else min(1.0, solar_elev / FULL_MARK_SOLAR_ELEV)

        # 4. Duration before Sunset (N/A -> 0, >= 14d -> 1.0)
        s_sunset = min(1.0, sunset_sec / FULL_MARK_DURATION_S) if (is_sunset and sunset_sec > 0) else 0.0

        # 5. Solar Irradiance (>= 1000 W/m² -> 1.0)
        s_irradiance = min(1.0, irradiance / FULL_MARK_IRRADIANCE)

        # 6. Lunar Diurnal Temperature Range (1.0 if inside user range; penalized by excess K)
        cold_excess = max(0.0, user_min_k - site_tmin)
        heat_excess = max(0.0, site_tmax - user_max_k)
        total_temp_excess = cold_excess + heat_excess
        s_temp = max(0.0, 1.0 - (total_temp_excess / TEMP_EXCEED_TOLERANCE))

        # 7. Slope Angle (<= 5° -> 1.0, >= 45° -> 0.0)
        if slope <= FULL_MARK_SLOPE_MAX:
            s_slope = 1.0
        elif slope >= ZERO_MARK_SLOPE_MIN:
            s_slope = 0.0
        else:
            s_slope = 1.0 - ((slope - FULL_MARK_SLOPE_MAX) / (ZERO_MARK_SLOPE_MIN - FULL_MARK_SLOPE_MAX))

        # 8. Water Ice Probability (Direct 0.0 to 1.0 index)
        s_ice = water_ice

        # Weighted Feasibility Percentage (0 - 100%)
        weighted_total = (
            s_earth_elev * CRITERIA_WEIGHTS["earth_elevation"] +
            s_earthset * CRITERIA_WEIGHTS["earthset_duration"] +
            s_solar_elev * CRITERIA_WEIGHTS["solar_elevation"] +
            s_sunset * CRITERIA_WEIGHTS["sunset_duration"] +
            s_irradiance * CRITERIA_WEIGHTS["solar_irradiance"] +
            s_temp * CRITERIA_WEIGHTS["diurnal_temp"] +
            s_slope * CRITERIA_WEIGHTS["slope_angle"] +
            s_ice * CRITERIA_WEIGHTS["water_ice"]
        ) * 100.0

        sites.append({
            "site_num": idx,
            "lat": lat,
            "lon": lon,
            "iso_utc": iso_utc,
            "display_id": display_id,
            "feasibility_pct": round(weighted_total, 2),
            "metrics": {
                "earth_elev": {"val": earth_elev, "display": f"{earth_elev:.2f}°", "score": s_earth_elev},
                "earthset": {
                    "is_valid": earth_valid,
                    "is_perpetual": earth_perpetual,
                    "seconds": earthset_sec,
                    "display": earth_display,
                    "note": earth_note,
                    "score": s_earthset
                },
                "solar_elev": {"val": solar_elev, "display": f"{solar_elev:.2f}°", "score": s_solar_elev},
                "sunset": {
                    "is_valid": sun_valid,
                    "is_perpetual": sun_perpetual,
                    "seconds": sunset_sec,
                    "display": sun_display,
                    "note": sun_note,
                    "score": s_sunset
                },
                "irradiance": {
                    "val": irradiance,
                    "display": "0.00 W/m² (Night)" if is_irr_na else f"{irradiance:.2f} W/m²",
                    "score": s_irradiance
                },
                "diurnal_temp": {
                    "tmin": site_tmin,
                    "tmax": site_tmax,
                    "excess": total_temp_excess,
                    "display": f"{site_tmin:.2f} K – {site_tmax:.2f} K",
                    "status": "Optimal Fit" if total_temp_excess == 0 else f"+{total_temp_excess:.2f} K Exceeded",
                    "score": s_temp
                },
                "slope": {"val": slope, "display": f"{slope:.2f}°", "score": s_slope},
                "water_ice": {"val": water_ice, "display": f"{water_ice:.2f}", "score": s_ice},
            }
        })

    # --- Dynamic Bar Scale Calculations ---
    # 1 & 3. Earth & Solar Elevation: fixed [-90, +90]
    for s in sites:
        s["metrics"]["earth_elev"]["bar_pct"] = round(((s["metrics"]["earth_elev"]["val"] + 90.0) / 180.0) * 100.0, 2)
        s["metrics"]["solar_elev"]["bar_pct"] = round(((s["metrics"]["solar_elev"]["val"] + 90.0) / 180.0) * 100.0, 2)

    # 2. Earthset Duration: min = 0, max = ceiling of days of longest site duration (capped at 400d)
    max_earthset_sec = max((s["metrics"]["earthset"]["seconds"] for s in sites if s["metrics"]["earthset"]["is_valid"]), default=0.0)
    earthset_ceil_days = min(400, max(1, math.ceil(max_earthset_sec / 86400.0))) if max_earthset_sec > 0 else 1
    earthset_bar_max_sec = earthset_ceil_days * 86400.0
    earthset_max_label = "≥ 400d" if any(s["metrics"]["earthset"]["is_perpetual"] for s in sites) else f"{earthset_ceil_days}d"

    for s in sites:
        if s["metrics"]["earthset"]["is_perpetual"]:
            s["metrics"]["earthset"]["bar_pct"] = 100.0
        elif s["metrics"]["earthset"]["is_valid"] and earthset_bar_max_sec > 0:
            s["metrics"]["earthset"]["bar_pct"] = round(min(100.0, (s["metrics"]["earthset"]["seconds"] / earthset_bar_max_sec) * 100.0), 2)
        else:
            s["metrics"]["earthset"]["bar_pct"] = 0.0

    # 4. Sunset Duration: min = 0, max = ceiling of days of longest site duration (capped at 400d)
    max_sunset_sec = max((s["metrics"]["sunset"]["seconds"] for s in sites if s["metrics"]["sunset"]["is_valid"]), default=0.0)
    sunset_ceil_days = min(400, max(1, math.ceil(max_sunset_sec / 86400.0))) if max_sunset_sec > 0 else 1
    sunset_bar_max_sec = sunset_ceil_days * 86400.0
    sunset_max_label = "≥ 400d" if any(s["metrics"]["sunset"]["is_perpetual"] for s in sites) else f"{sunset_ceil_days}d"

    for s in sites:
        if s["metrics"]["sunset"]["is_perpetual"]:
            s["metrics"]["sunset"]["bar_pct"] = 100.0
        elif s["metrics"]["sunset"]["is_valid"] and sunset_bar_max_sec > 0:
            s["metrics"]["sunset"]["bar_pct"] = round(min(100.0, (s["metrics"]["sunset"]["seconds"] / sunset_bar_max_sec) * 100.0), 2)
        else:
            s["metrics"]["sunset"]["bar_pct"] = 0.0

    # 5. Solar Irradiance: min = 0, max = highest value rounded up to nearest 10
    max_irr = max(s["metrics"]["irradiance"]["val"] for s in sites)
    irr_bar_max = max(10, int(math.ceil(max_irr / 10.0)) * 10)
    for s in sites:
        s["metrics"]["irradiance"]["bar_pct"] = round((s["metrics"]["irradiance"]["val"] / irr_bar_max) * 100.0, 2)

    # 6. Diurnal Temperature Range: dual-layer floating range bar (0 K to scale_max K)
    highest_temp = max([s["metrics"]["diurnal_temp"]["tmax"] for s in sites] + [user_max_k, 400.0])
    temp_bar_max = max(400, int(math.ceil(highest_temp / 10.0)) * 10)
    user_temp_left_pct = round((user_min_k / temp_bar_max) * 100.0, 2)
    user_temp_width_pct = round(((user_max_k - user_min_k) / temp_bar_max) * 100.0, 2)
    for s in sites:
        tmin = s["metrics"]["diurnal_temp"]["tmin"]
        tmax = s["metrics"]["diurnal_temp"]["tmax"]
        s["metrics"]["diurnal_temp"]["bar_left_pct"] = round((tmin / temp_bar_max) * 100.0, 2)
        s["metrics"]["diurnal_temp"]["bar_width_pct"] = max(1.5, round(((tmax - tmin) / temp_bar_max) * 100.0, 2))

    # 7 & 8. Slope [0, 90] and Water Ice [0, 1]
    for s in sites:
        s["metrics"]["slope"]["bar_pct"] = round((s["metrics"]["slope"]["val"] / 90.0) * 100.0, 2)
        s["metrics"]["water_ice"]["bar_pct"] = round(s["metrics"]["water_ice"]["val"] * 100.0, 2)

    # --- Assign Crowns to Highest Scorer(s) per Criterion ---
    metric_keys = ["earth_elev", "earthset", "solar_elev", "sunset", "irradiance", "diurnal_temp", "slope", "water_ice"]
    for key in metric_keys:
        best_score = max(s["metrics"][key]["score"] for s in sites)
        for s in sites:
            # Award crown to all sites tied for the highest positive score
            is_winner = (best_score > 0.0) and math.isclose(s["metrics"][key]["score"], best_score, abs_tol=1e-6)
            s["metrics"][key]["has_crown"] = is_winner

    # --- Rank Sites by Overall Feasibility Percentage (1st to 4th) ---
    ranked_sites = sorted(sites, key=lambda x: x["feasibility_pct"], reverse=True)
    rank_labels = ["1st", "2nd", "3rd", "4th"]
    for i, s in enumerate(ranked_sites):
        s["rank"] = i + 1
        s["rank_label"] = rank_labels[i]

    return {
        "sites": sites,                 # Ordered Site 1 -> Site 4 for bar charts
        "ranked_sites": ranked_sites,   # Ordered 1st -> 4th for standing podium
        "scales": {
            "earthset_max_label": f"{earthset_max_label}d",
            "sunset_max_label": f"{sunset_max_label}d",
            "irr_max_label": f"{irr_bar_max}",
            "temp_max_label": f"{temp_bar_max} K",
            "user_min_k": f"{user_min_k:.2f}",
            "user_max_k": f"{user_max_k:.2f}",
            "user_temp_left_pct": user_temp_left_pct,
            "user_temp_width_pct": user_temp_width_pct,
        },
        "weights": {k: int(v * 100) for k, v in CRITERIA_WEIGHTS.items()}
    }


@app.route('/compare_results', methods=['POST'])
def compare_results():
    if not request.is_json:
        return jsonify({"error": "Invalid JSON request"}), 400

    data = request.get_json()
    temp_range = data.get('temperature_range', {})
    user_min_k = float(temp_range.get('min_kelvin', 100.0))
    user_max_k = float(temp_range.get('max_kelvin', 350.0))

    raw_sites = []
    for i in range(1, 5):
        site_payload = data.get(f'site{i}', {})
        coord_str = site_payload.get('coordinate', '0.0, 0.0')
        lat_str, lon_str = coord_str.split(',')
        lat = float(lat_str.strip())
        lon = float(lon_str.strip())
        date_val = site_payload.get('date')
        time_val = site_payload.get('time')

        conditions = get_lunar_conditions(
            lat=lat,
            lon=lon,
            date=date_val,
            time_utc=time_val
        )
        raw_sites.append({
            "lat": lat,
            "lon": lon,
            "date": date_val,
            "time": time_val,
            "conditions": conditions
        })

    comparison_data = evaluate_and_rank_sites(raw_sites, user_min_k, user_max_k)
    return render_template('results.html', report=comparison_data)


@app.route('/results_simulation')
def results_simulation():
    return render_template('results_simulation.html')

from lunar_sim_service import build_simulation_payload

@app.route('/main_simulation')
def main_simulation():
    lat = request.args.get('lat', default=0.0, type=float)
    lon = request.args.get('lon', default=0.0, type=float)
    date_str = request.args.get('date', default='', type=str)
    time_str = request.args.get('time', default='00:00:00', type=str)
    return render_template(
        'main_simulation.html',
        active_page='simulation',
        lat=lat,
        lon=lon,
        date=date_str,
        time=time_str
    )

@app.route('/api/lunar-sim-data')
def api_lunar_sim_data():
    lat = request.args.get('lat', default=0.0, type=float)
    lon = request.args.get('lon', default=0.0, type=float)
    date_str = request.args.get('date', default='', type=str)
    time_str = request.args.get('time', default='00:00:00', type=str)
    try:
        payload = build_simulation_payload(lat, lon, date_str, time_str)
        return jsonify(payload)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500



if __name__ == '__main__':
    # Set debug=True during development for hot-reloading
    app.run(debug=True, port=5001)