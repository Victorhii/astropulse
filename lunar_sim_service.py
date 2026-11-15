#!/usr/bin/env python3
"""
lunar_sim_service.py
Builds fast (<3s), compact (<350 KB) 360-degree lunar horizon & terrain datasets
for the 1.7m first-person WebGL simulation using lunar_probe.py.
"""
from __future__ import annotations

import base64
import datetime
import functools
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import spiceypy as sp

import lunar_probe as lp

# Thread pool strictly for network HTTP Range reads (never for CSPICE calls)
SIM_POOL = ThreadPoolExecutor(max_workers=12)

# CSPICE uses global static C buffers and must be accessed by one thread at a time
SPICE_LOCK = threading.Lock()


def _ensure_clean_kernels(force_reload: bool = False) -> None:
    """Initializes SPICE kernels safely under SPICE_LOCK."""
    if force_reload:
        try:
            sp.kclear()
        except Exception:
            pass
        lp._kernels_ready = False
    if not lp._kernels_ready:
        try:
            sp.kclear()
        except Exception:
            pass
        lp.init_kernels()


def _fetch_dem_patch(lat: float, lon: float, radius_km: float, step: int = 1) -> dict:
    """
    Fetches a square LOLA 128 ppd elevation grid centered on (lat, lon) via HTTP Range
    requests using parallel row reads.
    """
    raster = lp.LDEM_128
    km_per_deg_lat = (math.pi / 180.0) * lp.R_MOON
    cphi = max(math.cos(math.radians(lat)), 0.05)
    km_per_deg_lon = km_per_deg_lat * cphi

    dlat = radius_km / km_per_deg_lat
    dlon = radius_km / km_per_deg_lon

    hr_raw = int(math.ceil(dlat * raster.ppd))
    hc_raw = int(math.ceil(dlon * raster.ppd))

    y0 = (90.0 - lat) * raster.ppd - 0.5
    x0 = (lon % 360.0) * raster.ppd - 0.5
    i_center = int(np.clip(round(y0), hr_raw, raster.nrows - 1 - hr_raw))
    j_center = int(round(x0))

    row_indices = list(range(i_center - hr_raw, i_center + hr_raw + 1, step))
    col_start = j_center - hc_raw
    col_count = 2 * hc_raw + 1

    rows_data = list(SIM_POOL.map(lambda r: raster._read(r, col_start, col_count), row_indices))
    grid = np.vstack(rows_data)[:, ::step].astype(np.float32) * raster.scale + raster.offset

    actual_cols = np.arange(col_start, col_start + col_count, step)
    actual_rows = np.array(row_indices)

    lats = 90.0 - (actual_rows + 0.5) / raster.ppd
    lons = ((actual_cols + 0.5) / raster.ppd) % 360.0
    lons = np.where(lons > 180.0, lons - 360.0, lons)

    return {
        "lats": lats.astype(np.float32),
        "lons": lons.astype(np.float32),
        "elev": grid.astype(np.float32),
        "rows": int(grid.shape[0]),
        "cols": int(grid.shape[1]),
    }


def _procedural_fallback_patch(lat: float, lon: float, radius_km: float, size: int = 96) -> dict:
    """Deterministic fallback DEM if PDS/MIT LOLA servers time out."""
    km_per_deg = (math.pi / 180.0) * lp.R_MOON
    cphi = max(math.cos(math.radians(lat)), 0.08)
    lats = np.linspace(lat + radius_km / km_per_deg, lat - radius_km / km_per_deg, size, dtype=np.float32)
    lons = np.linspace(lon - radius_km / (km_per_deg * cphi), lon + radius_km / (km_per_deg * cphi), size, dtype=np.float32)
    LON, LAT = np.meshgrid(lons, lats)
    dx = (LON - lon) * km_per_deg * cphi * 1000.0
    dy = (LAT - lat) * km_per_deg * 1000.0
    r = np.hypot(dx, dy)
    z = (
        180.0 * np.sin(dx / 4500.0) * np.cos(dy / 4500.0)
        + 65.0 * np.sin((dx + dy) / 1400.0)
        + np.where(r > 1800.0, 220.0 * np.exp(-((r - 3600.0) ** 2) / (2.0 * 900.0 ** 2)), -95.0 * (1.0 - (r / 1800.0) ** 2))
    )
    return {"lats": lats, "lons": lons, "elev": z.astype(np.float32), "rows": size, "cols": size}


def _compute_horizon_profile(site: lp.Site, h_obs_m: float, outer_patch: dict, n_bins: int = 360) -> np.ndarray:
    """Computes the exact 360-degree terrain horizon elevation angle (in degrees) around the observer."""
    lats = np.radians(outer_patch["lats"])[:, None]
    lons = np.radians(outer_patch["lons"])[None, :]
    elev = outer_patch["elev"]

    r_v = lp.R_MOON * 1000.0 + elev
    cos_lat = np.cos(lats)
    px = r_v * cos_lat * np.cos(lons)
    py = r_v * cos_lat * np.sin(lons)
    pz = r_v * np.broadcast_to(np.sin(lats), elev.shape)

    obs_pos = (lp.R_MOON * 1000.0 + h_obs_m + 1.7) * site.up
    dx = px - obs_pos[0]
    dy = py - obs_pos[1]
    dz = pz - obs_pos[2]

    east = dx * site.east[0] + dy * site.east[1] + dz * site.east[2]
    north = dx * site.north[0] + dy * site.north[1] + dz * site.north[2]
    up = dx * site.up[0] + dy * site.up[1] + dz * site.up[2]

    horiz_dist = np.hypot(east, north)
    valid = horiz_dist > 150.0
    el_deg = np.degrees(np.arctan2(up, np.maximum(horiz_dist, 1.0)))
    az_deg = (np.degrees(np.arctan2(east, north)) % 360.0)

    bins = np.floor(az_deg[valid] * (n_bins / 360.0)).astype(int) % n_bins
    els = el_deg[valid]

    horizon = np.full(n_bins, -10.0, dtype=np.float32)
    np.maximum.at(horizon, bins, els)
    horizon = 0.25 * np.roll(horizon, -1) + 0.5 * horizon + 0.25 * np.roll(horizon, 1)
    return horizon


@functools.lru_cache(maxsize=16)
def _cached_dem_bundle(lat_key: float, lon_key: float):
    """Caches inner (14 km) and outer (42 km) LOLA DEM patches per coordinate."""
    f_inner = SIM_POOL.submit(_fetch_dem_patch, lat_key, lon_key, 14.0, 1)
    f_outer = SIM_POOL.submit(_fetch_dem_patch, lat_key, lon_key, 42.0, 3)
    try:
        inner = f_inner.result(timeout=12.0)
        outer = f_outer.result(timeout=12.0)
        source = "NASA LRO LOLA 128ppd DEM"
    except Exception:
        inner = _procedural_fallback_patch(lat_key, lon_key, 14.0, 96)
        outer = _procedural_fallback_patch(lat_key, lon_key, 42.0, 96)
        source = "Synthetic Highland Fallback (LOLA Timeout)"
    return inner, outer, source


def _compute_spice_ephemeris_and_probe(lat: float, lon: float, date_str: str, time_str: str) -> dict:
    """Runs all CSPICE and lunar_probe calls sequentially under SPICE_LOCK."""
    utc_formatted = lp.parse_utc(date_str, time_str)
    et = sp.str2et(utc_formatted)
    site = lp.Site(lat, lon)

    # 1. Exact Sun & Earth topocentric positions and angular radii
    sun = lp._topo("SUN", [et], site, lp.R_SUN)
    earth = lp._topo("EARTH", [et], site, lp.R_EARTH)

    sun_el = float(sun["el"][0])
    sun_az = float(sun["az"][0])
    sun_rad_deg = float(sun["rad"][0])
    sun_dist_km = float(sun["dist"][0])

    earth_el = float(earth["el"][0])
    earth_az = float(earth["az"][0])
    earth_rad_deg = float(earth["rad"][0])
    earth_dist_km = float(earth["dist"][0])

    # 2. Earth illumination vector (Earth -> Sun direction in local ENU)
    v_sun_me = lp._vec("SUN", [et])[0]
    v_earth_me = lp._vec("EARTH", [et])[0]
    e_to_s = v_sun_me - v_earth_me
    e_to_s /= np.linalg.norm(e_to_s)
    earth_sun_enu = [
        float(e_to_s @ site.east),
        float(e_to_s @ site.up),
        float(-(e_to_s @ site.north)),
    ]

    # 3. Earth Greenwich Prime Meridian & North Pole in local ENU
    era = 2.0 * math.pi * (0.7790572732640 + 1.00273781191135448 * (et / 86400.0))
    j2000_to_me = sp.pxform("J2000", "MOON_ME", et)
    pm_me = j2000_to_me @ np.array([math.cos(era), math.sin(era), 0.0])
    np_me = j2000_to_me @ np.array([0.0, 0.0, 1.0])
    earth_pm_enu = [float(pm_me @ site.east), float(pm_me @ site.up), float(-(pm_me @ site.north))]
    earth_np_enu = [float(np_me @ site.east), float(np_me @ site.up), float(-(np_me @ site.north))]

    earth_to_moon_u = -earth["u"][0]
    cos_phase = float(np.clip(e_to_s @ earth_to_moon_u, -1.0, 1.0))
    earth_illum_frac = 0.5 * (1.0 + cos_phase)

    ecl = lp._eclipse_fraction(sun, earth, 0) if (sun_el + sun_rad_deg > 0) else 0.0
    irr = 0.0
    if sun_el + sun_rad_deg > 0 and ecl < 1.0:
        irr = lp.S0 * (lp.AU / sun_dist_km) ** 2 * (1.0 - ecl) * max(math.sin(math.radians(sun_el)), 0.0)

    # 4. Extended physical & rise/set conditions from lunar_probe.py
    probe = lp.get_lunar_conditions(lat, lon, date_str, time_str, budget_s=10.0)

    return {
        "utc_formatted": utc_formatted,
        "site": site,
        "sun_el": sun_el,
        "sun_az": sun_az,
        "sun_rad_deg": sun_rad_deg,
        "sun_dist_km": sun_dist_km,
        "earth_el": earth_el,
        "earth_az": earth_az,
        "earth_rad_deg": earth_rad_deg,
        "earth_dist_km": earth_dist_km,
        "earth_sun_enu": earth_sun_enu,
        "earth_pm_enu": earth_pm_enu,
        "earth_np_enu": earth_np_enu,
        "earth_illum_frac": earth_illum_frac,
        "ecl": ecl,
        "irr": irr,
        "probe": probe,
    }


def build_simulation_payload(lat: float, lon: float, date_str: str, time_str: str) -> dict:
    """
    Returns full ephemeris, true angular sizes, Earth phase/orientation vectors,
    lunar_probe physical conditions, and base64-packed Float32 DEM grids.
    """
    t0 = time.perf_counter()

    lat = float(np.clip(lat, -89.95, 89.95))
    lon = ((float(lon) + 180.0) % 360.0) - 180.0

    if not date_str or not date_str.strip():
        now = datetime.datetime.now(datetime.timezone.utc)
        date_str = now.strftime("%d/%m/%Y")
        if not time_str or not time_str.strip():
            time_str = now.strftime("%H:%M:%S")
    elif not time_str or not time_str.strip():
        time_str = "00:00:00"

    # Start HTTP Range LOLA DEM download in background thread pool (no SPICE calls here)
    dem_future = SIM_POOL.submit(_cached_dem_bundle, round(lat, 4), round(lon, 4))

    # Execute all SPICE operations sequentially on the main thread under SPICE_LOCK
    with SPICE_LOCK:
        _ensure_clean_kernels(force_reload=False)
        try:
            astro = _compute_spice_ephemeris_and_probe(lat, lon, date_str, time_str)
        except Exception as exc:
            if "BADFRAMESPEC2" in str(exc) or "SPICE" in str(exc):
                _ensure_clean_kernels(force_reload=True)
                astro = _compute_spice_ephemeris_and_probe(lat, lon, date_str, time_str)
            else:
                raise

    inner, outer, dem_source = dem_future.result()
    center_r = inner["rows"] // 2
    center_c = inner["cols"] // 2
    obs_ground_elev = float(inner["elev"][center_r, center_c])

    horizon_profile = _compute_horizon_profile(astro["site"], obs_ground_elev, outer, n_bins=360)
    sun_horizon_el = float(horizon_profile[int(round(astro["sun_az"])) % 360])
    earth_horizon_el = float(horizon_profile[int(round(astro["earth_az"])) % 360])

    probe = astro["probe"]
    water_ice_obj = probe.get("10_water_ice") or {}
    water_ice_val = water_ice_obj.get("stability_index_0to1", 0.0)

    elapsed_s = round(time.perf_counter() - t0, 3)
    slope_raw = probe.get("9_slope_deg")
    slope_val = float(slope_raw) if isinstance(slope_raw, (int, float)) and math.isfinite(slope_raw) else None

    return {
        "meta": {
            "lat": lat,
            "lon": lon,
            "date_input": date_str,
            "time_input": time_str,
            "utc": astro["utc_formatted"],
            "dem_source": dem_source,
            "observer_ground_elev_m": round(obs_ground_elev, 2),
            "eye_height_m": 1.70,
            "r_moon_m": lp.R_MOON * 1000.0,
            "compute_time_s": elapsed_s,
        },
        "astronomy": {
            "sun": {
                "az_deg": round(astro["sun_az"], 4),
                "el_deg": round(astro["sun_el"], 4),
                "angular_radius_deg": round(astro["sun_rad_deg"], 5),
                "angular_diameter_deg": round(astro["sun_rad_deg"] * 2.0, 5),
                "distance_km": round(astro["sun_dist_km"], 1),
                "irradiance_wm2": round(astro["irr"], 2),
                "eclipse_fraction": round(astro["ecl"], 4),
                "terrain_horizon_el_deg": round(sun_horizon_el, 3),
                "in_line_of_sight": bool((astro["sun_el"] + astro["sun_rad_deg"]) > sun_horizon_el),
                "event": probe.get("4_sun_event") or {},
            },
            "earth": {
                "az_deg": round(astro["earth_az"], 4),
                "el_deg": round(astro["earth_el"], 4),
                "angular_radius_deg": round(astro["earth_rad_deg"], 5),
                "angular_diameter_deg": round(astro["earth_rad_deg"] * 2.0, 5),
                "distance_km": round(astro["earth_dist_km"], 1),
                "illuminated_fraction": round(astro["earth_illum_frac"], 4),
                "sun_dir_enu": astro["earth_sun_enu"],
                "prime_meridian_enu": astro["earth_pm_enu"],
                "north_pole_enu": astro["earth_np_enu"],
                "terrain_horizon_el_deg": round(earth_horizon_el, 3),
                "in_line_of_sight": bool((astro["earth_el"] + astro["earth_rad_deg"]) > earth_horizon_el),
                "event": probe.get("5_earth_event") or {},
                "one_way_latency_s": probe.get("6_one_way_latency_s", round(astro["earth_dist_km"] / lp.C_KM_S, 4)),
                "geometric_latency_s": round(astro["earth_dist_km"] / lp.C_KM_S, 4),
            },
        },
        "surface_conditions": {
            "temperature_K": probe.get("7_surface_temperature_K", "N/A"),
            "lunation_tmin_tmax_K": probe.get("7b_lunation_Tmax_Tmin_K", [0, 0]),
            "slope_deg": slope_val,
            "water_ice_prob": float(water_ice_val) if isinstance(water_ice_val, (int, float)) else 0.0,
        },
        "horizon_profile_360": horizon_profile.round(3).tolist(),
        "terrain": {
            "inner": {
                "rows": inner["rows"],
                "cols": inner["cols"],
                "lat_min": float(inner["lats"][-1]),
                "lat_max": float(inner["lats"][0]),
                "lon_min": float(inner["lons"][0]),
                "lon_max": float(inner["lons"][-1]),
                "elev_b64": base64.b64encode(inner["elev"].tobytes()).decode("ascii"),
            },
            "outer": {
                "rows": outer["rows"],
                "cols": outer["cols"],
                "lat_min": float(outer["lats"][-1]),
                "lat_max": float(outer["lats"][0]),
                "lon_min": float(outer["lons"][0]),
                "lon_max": float(outer["lons"][-1]),
                "elev_b64": base64.b64encode(outer["elev"].tobytes()).decode("ascii"),
            },
        },
    }