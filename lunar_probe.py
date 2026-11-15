#!/usr/bin/env python3
"""
lunar_probe.py - one-call lunar surface conditions for any (lat, lon) and UTC time.

    from lunar_probe import get_lunar_conditions
    r = get_lunar_conditions(-85.0, 31.0, "12/11/2026", "14:30:00")

DATA SOURCES (NASA / NASA partners only)
  * NASA/NAIF SPICE kernels (de421 ephemeris + lunar orientation + leap seconds), ~20 MB total,
    downloaded ONCE and cached. A HEAD-request guard aborts if the total would exceed 30 MB.
    Valid for 1900-2050 (past, present and future).
  * LRO LOLA global DEM (PDS Geosciences Node, 128 px/deg). The 2 GB raster is NEVER downloaded:
    only a few dozen bytes are read with HTTP Range requests.
  * Surface temperature / ice stability: physical thermal model (Hayne et al. 2017 regolith
    parameters, calibrated on LRO Diviner) driven by exact SPICE illumination.
    Same code path for past, present and future.

Requires: pip install numpy scipy requests spiceypy
"""
from __future__ import annotations

import argparse
import math
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import requests
from scipy.linalg import solve_banded
from pathlib import Path

try:
    import spiceypy as sp
except ImportError:  # allows the thermal/DEM maths to be unit-tested without SPICE
    sp = None



# --------------------------------------------------------------------------- constants
R_MOON = 1737.4            # km, LOLA reference sphere
R_SUN = 695700.0           # km
R_EARTH = 6371.0           # km
AU = 149597870.7           # km
C_KM_S = 299792.458
S0 = 1360.8                # W/m^2 total solar irradiance at 1 AU (NASA SORCE/TSIS-1)
SIGMA = 5.670374419e-8
LUNATION = 29.530589 * 86400.0

# --------------------------------------------------------------------------- SPICE kernels
NAIF = "https://naif.jpl.nasa.gov/pub/naif/generic_kernels/"

# Each kernel is listed with one primary URL and one or more fallbacks.
# de421.bsp was relocated to the a_old_versions subfolder on NAIF; the
# JPL SSD mirror and a Skyfield cache mirror are kept as fallbacks.
KERNELS: dict[str, list[str]] = {
    "naif0012.tls": [
        NAIF + "lsk/naif0012.tls",
    ],
    "de421.bsp": [
        NAIF + "spk/planets/a_old_versions/de421.bsp",   # current NAIF path
        "https://ssd.jpl.nasa.gov/ftp/eph/planets/bsp/de421.bsp",   # JPL SSD mirror
        "https://raw.githubusercontent.com/skyfielders/python-skyfield/master/ci/de421.bsp",
    ],
    "moon_080317.tf": [
        NAIF + "fk/satellites/moon_080317.tf",
    ],
    "moon_pa_de421_1900-2050.bpc": [
        NAIF + "pck/moon_pa_de421_1900-2050.bpc",
    ],
}


# Adjust .parent depending on how deep this file is from your project root
BASE_DIR = Path(__file__).resolve().parent
KERNEL_DIR = BASE_DIR / "kernels"
KERNEL_LIMIT_MB = 30
_kernels_ready = False
T_MIN = T_MAX = None


def _download_kernel(name: str, urls: list[str]) -> None:
    """Try each URL in turn; save atomically only on success."""
    dest = os.path.join(KERNEL_DIR, name)
    part = dest + ".part"
    last_err = None
    for url in urls:
        try:
            with requests.get(url, stream=True, timeout=90) as r:
                r.raise_for_status()
                with open(part, "wb") as f:
                    for chunk in r.iter_content(1 << 20):
                        f.write(chunk)
            os.replace(part, dest)
            return                      # success
        except Exception as e:
            last_err = e
            if os.path.exists(part):
                os.remove(part)
    raise RuntimeError(f"Could not download kernel '{name}' from any URL. Last error: {last_err}")


def _probe_size(urls: list[str]) -> int:
    """Return Content-Length from the first URL that answers with 200/302."""
    for url in urls:
        try:
            h = requests.head(url, allow_redirects=True, timeout=15)
            if h.ok:
                return int(h.headers.get("Content-Length", 0))
        except Exception:
            continue
    return 0


def init_kernels():
    """Download (once, <=30 MB total) and load the SPICE kernels."""
    global _kernels_ready, T_MIN, T_MAX
    if _kernels_ready:
        return
    if sp is None:
        raise RuntimeError("spiceypy is not installed: pip install spiceypy")
    os.makedirs(KERNEL_DIR, exist_ok=True)
    missing = {n: u for n, u in KERNELS.items() if not os.path.exists(os.path.join(KERNEL_DIR, n))}
    if missing:
        total = sum(_probe_size(urls) for urls in missing.values())
        if total > KERNEL_LIMIT_MB * 1e6:
            raise RuntimeError(f"Kernel download would be {total/1e6:.1f} MB > {KERNEL_LIMIT_MB} MB limit")
        for name, urls in missing.items():
            _download_kernel(name, urls)
    for n in KERNELS:                      # LSK must be first (dict insertion order)
        sp.furnsh(os.path.join(KERNEL_DIR, n))
    T_MIN = sp.str2et("1900-06-01 00:00:00 TDB")
    T_MAX = sp.str2et("2049-12-31 00:00:00 TDB")
    _kernels_ready = True


# --------------------------------------------------------------------------- geometry
class Site:
    """Local East/North/Up frame at a lunar point in the Mean-Earth/Polar-Axis (ME) frame."""

    def __init__(self, lat, lon, h_km=0.0):
        p, l = math.radians(lat), math.radians(lon)
        self.up = np.array([math.cos(p) * math.cos(l), math.cos(p) * math.sin(l), math.sin(p)])
        self.east = np.array([-math.sin(l), math.cos(l), 0.0])
        self.north = np.cross(self.up, self.east)
        self.pos = (R_MOON + h_km) * self.up
        self.lat, self.lon = lat, lon


def _vec(body, ets):
    """Moon-centre -> body vector (km) in MOON_ME with light-time + stellar aberration."""
    ets = np.atleast_1d(np.asarray(ets, float))
    try:
        v, _ = sp.spkpos(body, ets.tolist(), "MOON_ME", "LT+S", "MOON")
        v = np.asarray(v, float).reshape(-1, 3)
        if len(v) == len(ets):
            return v
    except Exception:
        pass
    return np.array([sp.spkpos(body, float(t), "MOON_ME", "LT+S", "MOON")[0] for t in ets])


def _topo(body, ets, site, radius_km):
    v = _vec(body, ets) - site.pos
    dist = np.linalg.norm(v, axis=1)
    u = v / dist[:, None]
    e, n, up = u @ site.east, u @ site.north, u @ site.up
    return {
        "el": np.degrees(np.arcsin(np.clip(up, -1, 1))),
        "az": np.degrees(np.arctan2(e, n)) % 360.0,
        "dist": dist, "u": u, "enu": np.stack([e, n, up], 1),
        "rad": np.degrees(np.arcsin(radius_km / dist)),          # angular radius, deg
    }


def _overlap_fraction(rs, re_, d):
    """Fraction of a disc (radius rs) covered by another disc (re_) at separation d (all deg)."""
    if d >= rs + re_:
        return 0.0
    if d <= abs(re_ - rs):
        return 1.0 if re_ >= rs else (re_ / rs) ** 2
    a1 = rs * rs * math.acos((d * d + rs * rs - re_ * re_) / (2 * d * rs))
    a2 = re_ * re_ * math.acos((d * d + re_ * re_ - rs * rs) / (2 * d * re_))
    a3 = 0.5 * math.sqrt((-d + rs + re_) * (d + rs - re_) * (d - rs + re_) * (d + rs + re_))
    return (a1 + a2 - a3) / (math.pi * rs * rs)


def _eclipse_fraction(sun, earth, i):
    """Fraction of the solar disc hidden by Earth (lunar eclipse) at sample i."""
    sep = math.degrees(math.acos(np.clip(sun["u"][i] @ earth["u"][i], -1, 1)))
    return _overlap_fraction(sun["rad"][i], earth["rad"][i], sep)


# --------------------------------------------------------------------------- rise/set search
_CHUNKS = [(3, 600.0), (42, 1800.0), (355, 10800.0)]     # (span days, step s) -> 400 days max


def _next_transition(body, radius_km, et0, site):
    """Return (currently_visible, seconds_to_next_change or None). Visible = any part of disc above horizon."""
    def margin(ts):
        t = _topo(body, ts, site, radius_km)
        return t["el"] + t["rad"]

    state = margin([et0])[0] > 0
    start = et0
    for span, step in _CHUNKS:
        ts = start + np.arange(0.0, span * 86400.0 + step, step)
        ts = ts[ts <= T_MAX]
        if ts.size < 2:
            break
        flips = np.nonzero((margin(ts) > 0) != state)[0]
        if flips.size:
            lo, hi = ts[flips[0] - 1], ts[flips[0]]
            for _ in range(26):                                  # bisection to << 1 s
                mid = 0.5 * (lo + hi)
                if (margin([mid])[0] > 0) == state:
                    lo = mid
                else:
                    hi = mid
            return state, hi - et0
        start = ts[-1]
    return state, None


def _hms(sec):
    sec = int(round(sec))
    d, r = divmod(sec, 86400)
    h, r = divmod(r, 3600)
    m, s = divmod(r, 60)
    return (f"{d}d " if d else "") + f"{h:02d}:{m:02d}:{s:02d}"


def _event(visible, secs, names, never_msg_up, never_msg_down):
    if secs is None:
        return {"event": None, "seconds": None,
                "note": never_msg_up if visible else never_msg_down}
    return {"event": names[0] if visible else names[1], "seconds": round(secs, 1), "hms": _hms(secs)}


# --------------------------------------------------------------------------- LOLA DEM by HTTP Range
SESSION = requests.Session()
SESSION.mount("https://", requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=16))
_POOL = ThreadPoolExecutor(max_workers=8)


class RangeRaster:
    """Headerless, pixel-registered cylindrical raster (PDS .IMG) read via HTTP Range requests."""

    def __init__(self, urls, ncols, nrows, ppd, dtype="<i2", scale=1.0, offset=0.0, timeout=(5.0, 10.0)):
        self.urls, self.ncols, self.nrows, self.ppd = list(urls), ncols, nrows, ppd
        self.dtype, self.scale, self.offset, self.timeout = np.dtype(dtype), scale, offset, timeout
        self.isz = self.dtype.itemsize
        self._good = 0

    def _get(self, sample_off, n):
        a = sample_off * self.isz
        b = a + n * self.isz - 1
        last = None
        for k in range(self._good, len(self.urls)):
            try:
                r = SESSION.get(self.urls[k], headers={"Range": f"bytes={a}-{b}"},
                                timeout=self.timeout, stream=True)
                try:
                    if r.status_code != 206:
                        raise RuntimeError(f"HTTP {r.status_code}: no Range support (refusing full download)")
                    data = r.content
                finally:
                    r.close()
                if len(data) != n * self.isz:
                    raise RuntimeError("short read")
                self._good = k
                return np.frombuffer(data, dtype=self.dtype)
            except Exception as e:  # try next mirror
                last = e
        raise RuntimeError(f"raster read failed: {last}")

    def _read(self, row, col, n):
        col %= self.ncols
        first = min(n, self.ncols - col)
        parts = [self._get(row * self.ncols + col, first)]
        if first < n:
            parts.append(self._get(row * self.ncols, n - first))
        return np.concatenate(parts)

    def window(self, lat, lon, hr, hc):
        y = (90.0 - lat) * self.ppd - 0.5            # continuous row index (integer = pixel centre)
        x = (lon % 360.0) * self.ppd - 0.5
        i_n = int(np.clip(round(y), hr, self.nrows - 1 - hr))
        j_n = int(round(x))
        rows = range(i_n - hr, i_n + hr + 1)
        W = np.vstack(list(_POOL.map(lambda r: self._read(r, j_n - hc, 2 * hc + 1), rows)))
        return W.astype(float) * self.scale + self.offset, i_n, j_n, y, x

    @staticmethod
    def bilinear(W, y, x, i_n, j_n, hr, hc):
        r = int(np.clip(math.floor(y) - (i_n - hr), 0, 2 * hr - 1))
        c = int(np.clip(math.floor(x) - (j_n - hc), 0, 2 * hc - 1))
        fy = float(np.clip(y - (i_n - hr + r), 0, 1))
        fx = float(np.clip(x - (j_n - hc + c), 0, 1))
        return (W[r, c] * (1 - fy) * (1 - fx) + W[r, c + 1] * (1 - fy) * fx
                + W[r + 1, c] * fy * (1 - fx) + W[r + 1, c + 1] * fy * fx)


_LDEM_NAMES = [
    "https://pds-geosciences.wustl.edu/lro/lro-l-lola-3-rdr-v1/lrolol_1xxx/data/lola_gdr/cylindrical/img/ldem_{r}.img",
    "https://imbrium.mit.edu/DATA/LOLA_GDR/CYLINDRICAL/IMG/LDEM_{r}.IMG",
]
# DN * 0.5 m = height above the 1737.4 km sphere (LSB int16)
LDEM_128 = RangeRaster([u.format(r=128) for u in _LDEM_NAMES], 46080, 23040, 128, "<i2", 0.5)

# Optional composition rasters (FeO, Th, H ...). Fill in after checking the PDS .LBL, e.g.
#   COMPOSITION["FeO_wt_pct"] = RangeRaster([url], ncols, nrows, ppd, "<f4", 1.0)
COMPOSITION: dict[str, RangeRaster] = {}


def sample_dem(lat, lon, raster=LDEM_128):
    """Elevation (m), slope (deg), aspect (deg), and surface normal in local ENU. One burst of tiny Range reads."""
    cphi = max(math.cos(math.radians(lat)), 1e-6)
    s = int(np.clip(round(1.0 / max(cphi, 1e-3)), 1, 32))       # widen E-W baseline near the poles
    W, i_n, j_n, y, x = raster.window(lat, lon, 1, s)
    h = raster.bilinear(W, y, x, i_n, j_n, 1, s)
    d = math.pi / 180.0 / raster.ppd * R_MOON * 1000.0           # metres per pixel along a meridian
    dy, dx = d, s * d * cphi
    a, b, c = W[0, 0], W[0, s], W[0, 2 * s]
    dd, f = W[1, 0], W[1, 2 * s]
    g, hh, i = W[2, 0], W[2, s], W[2, 2 * s]
    p = ((c + 2 * f + i) - (a + 2 * dd + g)) / (8 * dx)           # dz/dEast
    q = ((a + 2 * b + c) - (g + 2 * hh + i)) / (8 * dy)           # dz/dNorth
    n = np.array([-p, -q, 1.0])
    n /= np.linalg.norm(n)
    return {"elevation_m": float(h), "slope_deg": math.degrees(math.atan(math.hypot(p, q))),
            "aspect_deg": math.degrees(math.atan2(-p, -q)) % 360.0, "normal_enu": n,
            "baseline_m": float(max(dx, dy) * 2)}


# --------------------------------------------------------------------------- thermal model
# Hayne et al. (2017, JGR Planets) two-layer regolith; Vasavada et al. (2012) heat capacity & albedo law.
RHO_S, RHO_D, H_SCALE = 1100.0, 1800.0, 0.06
KC_S, KC_D, CHI = 7.4e-4, 3.4e-3, 2.7
EMISS, Q_GEO = 0.95, 0.018


def _cp(T):
    T = np.maximum(T, 20.0)
    return 8.9093e-9 * T**4 - 1.2340e-5 * T**3 + 2.3616e-3 * T**2 + 2.7431 * T - 3.6125


def _grid(n=30, dz0=0.0015, g=1.17):
    dz = dz0 * g ** np.arange(n)
    z = np.cumsum(dz) - dz / 2
    return dz, z


def run_thermal(ts, Q, T_init=None):
    """Implicit 1-D heat conduction, Newton-consistent radiative surface BC.
    ts: times (s), Q: absorbed solar flux per time (W/m^2). Returns surface T and 0.1 m T series."""
    dz, z = _grid()
    N = len(dz)
    rho = RHO_D - (RHO_D - RHO_S) * np.exp(-z / H_SCALE)
    kc = KC_D - (KC_D - KC_S) * np.exp(-z / H_SCALE)
    i10 = int(np.argmin(abs(z - 0.1)))
    if T_init is None:
        T_init = max(0.88 * (max(np.mean(Q), 1e-9) / (EMISS * SIGMA)) ** 0.25, 40.0)
    T = np.full(N, float(T_init))
    Ts = T[0]
    Ts_h, T10_h = np.empty(len(ts)), np.empty(len(ts))
    Ts_h[0], T10_h[0] = Ts, T[i10]
    ab = np.zeros((3, N))
    for k in range(1, len(ts)):
        dt = ts[k] - ts[k - 1]
        kk = kc * (1.0 + CHI * (T / 350.0) ** 3)
        C = rho * _cp(T) * dz / dt
        K = 1.0 / (0.5 * dz[:-1] / kk[:-1] + 0.5 * dz[1:] / kk[1:])
        G = kk[0] / (0.5 * dz[0])
        q = Q[k]
        Ts = max(T[0], (q / (EMISS * SIGMA)) ** 0.25)              # start right of the root -> monotone Newton
        for _ in range(8):
            f = q + G * (T[0] - Ts) - EMISS * SIGMA * Ts**4
            Ts -= f / (-G - 4 * EMISS * SIGMA * Ts**3)
        rad = 4 * EMISS * SIGMA * Ts**3
        Hc = G * rad / (G + rad)
        diag = C.copy()
        diag[:-1] += K
        diag[1:] += K
        diag[0] += Hc
        rhs = C * T
        rhs[0] += G * (Ts - T[0]) + Hc * T[0]
        rhs[-1] += Q_GEO
        ab[1] = diag
        ab[0, 1:] = -K
        ab[2, :-1] = -K
        T = solve_banded((1, 1), ab, rhs)
        Ts_h[k], T10_h[k] = Ts, T[i10]
    return Ts_h, T10_h


def _albedo(cos_i, A0):
    th = np.degrees(np.arccos(np.clip(cos_i, 0, 1)))
    return A0 + 0.045 * (th / 45.0) ** 3 + 0.14 * (th / 90.0) ** 8


def _sublimation_lifetime_years(T):
    """Lifetime of a 1 m water-ice slab at a given temperature history (Murphy & Koop 2005 vapour pressure)."""
    T = np.maximum(T, 30.0)
    Pv = np.exp(9.550426 - 5723.265 / T + 3.53068 * np.log(T) - 0.00728332 * T)    # Pa
    E = Pv * np.sqrt(0.018015 / (2 * math.pi * 8.314462 * T))                      # kg m^-2 s^-1
    return 930.0 / max(float(np.mean(E)), 1e-300) / 3.156e7


def thermal_and_ice(site, et, normal, A0=0.12, n_spin=8):
    n_fine = int(LUNATION // 1800) + 48
    t_fine = et - np.arange(n_fine, -1, -1) * 1800.0
    n_coarse = int(n_spin * LUNATION // 10800)
    t_coarse = t_fine[0] - 10800.0 * np.arange(n_coarse, 0, -1)
    ts = np.concatenate([t_coarse, t_fine])
    sun = _topo("SUN", ts, site, R_SUN)
    E = S0 * (AU / sun["dist"]) ** 2
    cos_i = sun["enu"] @ normal if normal is not None else np.sin(np.radians(sun["el"]))
    lit = sun["el"] + sun["rad"] > 0
    cos_i = np.where(lit, np.clip(cos_i, 0, 1), 0.0)
    vis = np.ones(len(ts))
    last = np.nonzero(ts >= et - 12 * 3600)[0]                    # Earth shadow (lunar eclipse) in the last 12 h
    earth = _topo("EARTH", ts[last], site, R_EARTH)
    sun_l = {k: v[last] for k, v in sun.items()}
    for j, idx in enumerate(last):
        vis[idx] = 1.0 - _eclipse_fraction(sun_l, earth, j)
    Q = (1 - _albedo(cos_i, A0)) * E * cos_i * vis
    Ts, T10 = run_thermal(ts, Q)
    lun = ts >= et - LUNATION
    life_buried = _sublimation_lifetime_years(T10[lun])
    life_surface = _sublimation_lifetime_years(Ts[lun])
    x = math.log10(max(life_buried, 1.0) / 1e9)
    return {
        "surface_temperature_K": float(Ts[-1]),
        "tmax_lunation_K": float(Ts[lun].max()), "tmin_lunation_K": float(Ts[lun].min()),
        "t_at_10cm_mean_K": float(T10[lun].mean()), "t_at_10cm_max_K": float(T10[lun].max()),
        "ice_lifetime_1m_at_10cm_yr": life_buried, "ice_lifetime_1m_surface_yr": life_surface,
        "ice_stability_index": 1.0 / (1.0 + math.exp(-2.0 * x)),
    }


# --------------------------------------------------------------------------- public API
def parse_utc(date, time_utc):
    dd, mm, yy = [int(v) for v in re.split(r"[/\-.]", date.strip())]
    parts = [float(v) for v in re.split(r"[:/.\-\s]+", time_utc.strip())] + [0.0, 0.0]
    hh, mi, ss = int(parts[0]), int(parts[1]), parts[2]
    return f"{yy:04d}-{mm:02d}-{dd:02d} {hh:02d}:{mi:02d}:{ss:06.3f} UTC"


def get_lunar_conditions(lat, lon, date, time_utc, *, albedo=0.12, budget_s=10.0, use_slope_in_thermal=True):
    """lat/lon: selenographic degrees (ME frame; lon +E, -180..360 accepted).
    date 'DD/MM/YYYY', time 'HH:MM:SS' (UTC). Returns a dict; unavailable fields are 'N/A'."""
    t0 = time.perf_counter()
    init_kernels()
    if not -90 <= lat <= 90:
        raise ValueError("lat must be in [-90, 90]")
    et = sp.str2et(parse_utc(date, time_utc))
    if not (T_MIN + 300 * 86400 <= et <= T_MAX):
        raise ValueError("date outside supported window 1901-04 .. 2049-12 (de421 coverage)")
    lon = ((lon + 180.0) % 360.0) - 180.0
    site = Site(lat, lon)
    timing = {}

    dem_future = _POOL.submit(sample_dem, lat, lon)                 # network runs while we compute

    # ---- current geometry
    sun = _topo("SUN", [et], site, R_SUN)
    earth = _topo("EARTH", [et], site, R_EARTH)
    sun_vis = bool(sun["el"][0] + sun["rad"][0] > 0)
    earth_vis = bool(earth["el"][0] + earth["rad"][0] > 0)
    ecl = _eclipse_fraction(sun, earth, 0) if sun_vis else 0.0
    irr = "N/A"
    if sun_vis and ecl < 1.0:
        irr = S0 * (AU / sun["dist"][0]) ** 2 * (1 - ecl) * max(math.sin(math.radians(sun["el"][0])), 0.0)
    latency = "N/A"
    if earth_vis:
        latency = earth["dist"][0] / C_KM_S                      # site -> Earth centre

    # ---- rise / set events
    sv, ss = _next_transition("SUN", R_SUN, et, site)
    ev, es = _next_transition("EARTH", R_EARTH, et, site)
    timing["ephemeris_s"] = round(time.perf_counter() - t0, 3)

    # ---- DEM
    dem, dem_err = None, None
    try:
        dem = dem_future.result(timeout=max(0.3, budget_s - (time.perf_counter() - t0) - 1.2))
    except Exception as e:
        dem_err = str(e)[:120]
    timing["dem_s"] = round(time.perf_counter() - t0, 3)

    # ---- thermal + ice proxy (shorter spin-up if time is tight)
    remaining = budget_s - (time.perf_counter() - t0)
    th = thermal_and_ice(site, et, dem["normal_enu"] if (dem and use_slope_in_thermal) else None,
                         A0=albedo, n_spin=8 if remaining > 1.9 else 4)
    timing["total_s"] = round(time.perf_counter() - t0, 3)

    # ---- regolith
    comp = {}
    for name, rs in COMPOSITION.items():
        try:
            W, i_n, j_n, y, x = rs.window(lat, lon, 1, 1)
            comp[name] = float(rs.bilinear(W, y, x, i_n, j_n, 1, 1))
        except Exception as e:
            comp[name] = f"N/A ({str(e)[:60]})"
    feo = comp.get("FeO_wt_pct")
    if isinstance(feo, float):
        cls = "mare basalt" if feo >= 14 else ("highland (anorthositic)" if feo <= 10 else "mixed / transitional")
        thick = "~3-6 m" if cls == "mare basalt" else ("~10-15 m" if cls.startswith("highland") else "~5-10 m")
    else:
        cls, thick = "undetermined (no composition raster configured)", "N/A"

    r = {
        "input": {"lat": lat, "lon_E": lon, "utc": parse_utc(date, time_utc)},
        "1_solar_elevation_deg": round(float(sun["el"][0]), 4),
        "1b_solar_azimuth_deg": round(float(sun["az"][0]), 3),
        "2_earth_elevation_deg": round(float(earth["el"][0]), 4),
        "2b_earth_azimuth_deg": round(float(earth["az"][0]), 3),
        "3_solar_irradiance_W_m2": round(irr, 2) if irr != "N/A" else "N/A",
        "3b_solar_disc_eclipsed_by_Earth": round(ecl, 4),
        "4_sun_event": _event(sv, ss, ("sunset", "sunrise"),
                              "Sun stays up for >=400 days (no sunset)", "Sun stays down for >=400 days (no sunrise)"),
        "5_earth_event": _event(ev, es, ("earthset", "earthrise"),
                                "Earth never sets within 400 days (near-side)", "Earth never rises within 400 days (far-side)"),
        "6_one_way_latency_s": round(latency, 5) if latency != "N/A" else "N/A",
        "7_surface_temperature_K": round(th["surface_temperature_K"], 1),
        "7b_lunation_Tmax_Tmin_K": (round(th["tmax_lunation_K"], 1), round(th["tmin_lunation_K"], 1)),
        "8_elevation_m": round(dem["elevation_m"], 1) if dem else f"N/A ({dem_err})",
        "9_slope_deg": round(dem["slope_deg"], 2) if dem else "N/A",
        "9b_slope_aspect_deg": round(dem["aspect_deg"], 1) if dem else "N/A",
        "10_water_ice": {
            "stability_index_0to1": round(th["ice_stability_index"], 3),
            "T_at_10cm_mean_K": round(th["t_at_10cm_mean_K"], 1),
            "ice_lifetime_1m_at_10cm_yr": float(f"{th['ice_lifetime_1m_at_10cm_yr']:.3g}"),
            "note": "thermal-stability PROXY on a flat/average-slope surface; ignores terrain shadowing (PSRs)",
        },
        "11_regolith": {"class": cls, "typical_thickness": thick, "composition": comp or "N/A",
                        "thermophysical_model": {"rho_surface": RHO_S, "rho_deep": RHO_D, "H_m": H_SCALE,
                                                 "k_c_surface": KC_S, "k_c_deep": KC_D}},
        "timing_s": timing,
    }
    return r


def pretty(r):
    for k, v in r.items():
        print(f"{k:34s} {v}")


# --------------------------------------------------------------------------- validation helpers
def horizons_check(lat, lon, date, time_utc):
    """Cross-check the Sun's elevation against JPL Horizons (network, ~1-2 s, NOT part of the fast path)."""
    ut = parse_utc(date, time_utc)[:16]
    t2 = (np.datetime64(ut.replace(" ", "T")) + np.timedelta64(1, "m")).astype(str).replace("T", " ")[:16]
    q = {"format": "text", "COMMAND": "'10'", "CENTER": "'coord@301'", "COORD_TYPE": "'GEODETIC'",
         "SITE_COORD": f"'{lon},{lat},0'", "MAKE_EPHEM": "YES", "EPHEM_TYPE": "OBSERVER",
         "START_TIME": f"'{ut}'", "STOP_TIME": f"'{t2}'", "STEP_SIZE": "'1m'", "QUANTITIES": "'4'",
         "CSV_FORMAT": "YES", "ANG_FORMAT": "DEG"}
    txt = requests.get("https://ssd.jpl.nasa.gov/api/horizons.api", params=q, timeout=20).text
    return txt.split("$$SOE")[-1].split("$$EOE")[0].strip() if "$$SOE" in txt else txt[:500]


def check_endpoints():
    """Probe every remote dependency and report status / latency."""
    for n, urls in KERNELS.items():
        for idx, u in enumerate(urls):
            tag = "primary" if idx == 0 else f"fallback{idx}"
            t = time.perf_counter()
            try:
                h = requests.head(u, allow_redirects=True, timeout=10)
                mb = int(h.headers.get("Content-Length", 0)) / 1e6
                print(f"[{h.status_code}] {n:38s} ({tag:9s}) {mb:6.1f} MB  {time.perf_counter()-t:.2f}s")
            except Exception as e:
                print(f"[ERR] {n} ({tag}): {e}")
    t = time.perf_counter()
    try:
        d = sample_dem(-85.0, 31.0)
        print(f"[OK ] LOLA DEM range-read  h={d['elevation_m']:.0f} m slope={d['slope_deg']:.1f} deg  {time.perf_counter()-t:.2f}s")
    except Exception as e:
        print(f"[ERR] LOLA DEM: {e}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--lat", type=float)
    ap.add_argument("--lon", type=float)
    ap.add_argument("--date", help="DD/MM/YYYY")
    ap.add_argument("--time", default="00:00:00", help="HH:MM:SS UTC")
    ap.add_argument("--check", action="store_true", help="probe remote endpoints")
    ap.add_argument("--horizons", action="store_true", help="cross-check sun elevation with JPL Horizons")
    a = ap.parse_args()
    if a.check:
        check_endpoints()
        sys.exit()
    res = get_lunar_conditions(a.lat, a.lon, a.date, a.time)
    pretty(res)
    if a.horizons:
        print("\nHorizons (Sun az/el):", horizons_check(a.lat, a.lon, a.date, a.time))