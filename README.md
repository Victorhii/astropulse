# 🌕 Astropulse — Lunar Landing Site Navigator

> **2026 NASA Space Apps Challenge** | Category: Lunar Landing Site Navigator | Difficulty: Advanced Intermediate

Astropulse is a full-stack web application that enables rapid, data-driven evaluation of lunar landing sites. It consolidates NASA SPICE ephemerides, LRO LOLA terrain data, and a physics-based thermal model into one accessible platform — serving mission planners, educators, and the general public alike.

**Live Demo:** `https://astropulse-ten.vercel.app` 
**Challenge Brief:** [Lunar Landing Site Navigator — NASA Space Apps 2026](https://www.spaceappschallenge.org/)

---

## 📋 Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Project Structure](#project-structure)
- [Pages](#pages)
- [Tech Stack](#tech-stack)
- [Data Sources](#data-sources)
- [Installation & Local Setup](#installation--local-setup)
- [Deployment on Vercel](#deployment-on-vercel)
- [API Endpoints](#api-endpoints)
- [Scientific Methods](#scientific-methods)
- [AI Disclosure](#ai-disclosure)
- [Resources & Attributions](#resources--attributions)
- [License](#license)

---

## Overview

Missions to the Moon's south pole face extreme lighting and communication constraints. Tools like NASA Quickmap and JPL Horizons are powerful but difficult to use for rapid scenario comparison. Astropulse bridges that gap by translating complex ephemeris and topographic data into clear, comparable visualizations — enabling agile decision-making when hardware delays or launch slips force planners to reconsider a landing date or location.

---

## Features

### 🛰️ Dashboard
- Live UTC clock synced to [timeapi.io](https://timeapi.io)
- Real-time 3D Moon globe rendered with Three.js and an 8K NASA-calibrated texture from Solar System Scope
- Accurate lunar phase and day counter based on synodic month calculations
- Real Sun lighting and optical libration computed from orbital formulas

### 🔍 Site Comparison (Query → Results)
- **Four-site input** with UTC date and time per site
- **Three coordinate input methods:** interactive 3D lunar globe with landmark labels, searchable catalogue of past and planned missions, or manual lat/lon entry
- **Eight weighted evaluation criteria** scored 0–100%:
  | # | Criterion | Weight |
  |---|-----------|--------|
  | 1 | Earth Elevation Angle | 10% |
  | 2 | Duration Before Earthset | 10% |
  | 3 | Solar Elevation Angle | 10% |
  | 4 | Duration Before Sunset | 15% |
  | 5 | Solar Irradiance (W/m²) | 15% |
  | 6 | Lunar Diurnal Temperature Range | 15% |
  | 7 | Terrain Slope Angle | 15% |
  | 8 | Water Ice Stability Index | 10% |
- **Feasibility podium** ranking all four sites with gold/silver/bronze/fourth medals
- **Crown icons** marking the top-scoring site per criterion
- **"Why It Matters?"** plain-language explanation per criterion
- **DTE alert** when Earth line of sight is blocked and a relay satellite is needed
- **Colourful PDF export** via html2pdf.js (landscape A4)

### 🌐 Simulation (Selection → Main)
- **Selection page:** rotating 3D globe with three switchable surface layers — optical, elevation, slope angle, and water ice stability — driven by a custom GLSL shader and the LOLA global DEM
- **Main simulation:** first-person WebGL view at 1.7 m eye height on real LOLA terrain
- Accurate Sun and Earth rendered at their true angular sizes, positions, and phases, computed from NASA SPICE ephemerides
- 360-degree terrain horizon profile computed from a 42 km LOLA patch
- Real-time DTE relay alert when Earth is below the terrain horizon
- **Telemetry panel:** solar az/el, Earth az/el, sightlines, Earth phase, irradiance, signal latency, slope, temperature, water ice probability
- **Timeline mode:** scrubber over a user-defined range (up to 399 days) with overlaid sunlight and Direct-to-Earth windows
- FOV and exposure sliders; snap-to-sky camera button

### 📚 Knowledge Hub
- Six lunar environment challenges: two-week nights, extreme heat and cold, Earth signal blackouts, long polar shadows, the water-ice paradox, and steep slopes with sharp dust
- **Interactive Sensitivity Explorer:** three scenarios (latitude shift, 5 km micro-shift, 7-day launch delay) showing how small changes transform power, temperature, and communication conditions
- Core trade-offs section explaining why no single site scores 100% across all criteria

---

## Project Structure

```
astropulse/
│
├── app.py                      # Flask application, API routes, scoring engine
├── lunar_probe.py              # NASA SPICE + LOLA backend: ephemeris, terrain, thermal, ice
├── lunar_sim_service.py        # Simulation payload builder (DEM patches, horizon profile)
│
├── templates/
│   ├── navigation.html         # Shared sidebar navigation partial
│   ├── dashboard.html          # Live Moon globe & UTC clock
│   ├── query.html              # Four-site input form
│   ├── results.html            # Feasibility comparison & PDF export
│   ├── simulation.html         # Globe layer selection page
│   ├── main_simulation.html    # First-person lunar surface simulation
│   └── knowledge.html          # Educational hub & sensitivity explorer
│
├── static/
│   ├── images/                 # Mission site images, cached lunar texture
│   └── ...
│
├── kernels/                    # NASA NAIF SPICE kernels (auto-downloaded on first run)
│   ├── naif0012.tls
│   ├── de421.bsp
│   ├── moon_080317.tf
│   └── moon_pa_de421_1900-2050.bpc
│
├── requirements.txt
├── vercel.json
├── AI_DISCLOSURE.md
└── README.md
```

---

## Pages

| Route | Page | Description |
|-------|------|-------------|
| `/` or `/dashboard` | Dashboard | Live Moon globe, UTC clock, lunar day |
| `/query` | Query | Four-site input form |
| `/compare_results` | Results | Feasibility comparison (POST) |
| `/simulation` | Simulation Selection | Globe with surface layers |
| `/main_simulation` | Main Simulation | First-person lunar surface view |
| `/knowledge` | Knowledge Hub | Education & sensitivity explorer |

---

## Tech Stack

### Backend
| Tool | Purpose |
|------|---------|
| Python 3.11+ | Core language |
| Flask | Web framework and API routing |
| SpiceyPy | Python wrapper for NASA NAIF SPICE |
| NumPy | Numerical computation |
| SciPy | Implicit thermal model solver (`solve_banded`) |
| Requests | HTTP Range reads for LOLA DEM |

### Frontend
| Tool | Version | Purpose |
|------|---------|---------|
| Three.js | r128, 0.160, 0.170 | 3D Moon globes and simulation |
| GLSL Shaders | — | Surface layer colouring, terrain, sky |
| flatpickr | latest | Date picker |
| html2pdf.js | 0.10.1 | PDF export |
| Font Awesome Free | 6.4–6.5 | Icons |
| Orbitron (Google Fonts) | — | Telemetry panel typography |
| DSEG | 0.46.0 | LCD-style digits in simulation |

### Deployment
| Tool | Purpose |
|------|---------|
| Vercel | Hosting and serverless deployment |
| Visual Studio Code | Development environment |

---

## Data Sources

| Source | Used for | Link |
|--------|---------|------|
| NASA/NAIF SPICE kernels | Sun & Earth positions, Moon orientation, leap seconds | https://naif.jpl.nasa.gov/pub/naif/generic_kernels/ |
| JPL SSD de421 ephemeris | Fallback kernel download | https://ssd.jpl.nasa.gov/ftp/eph/planets/bsp/de421.bsp |
| JPL Horizons API | Sun elevation cross-check | https://ssd.jpl.nasa.gov/api/horizons.api |
| LRO LOLA DEM 128 ppd | Site elevation, slope, horizon profile, terrain mesh | https://pds-geosciences.wustl.edu/lro/lro-l-lola-3-rdr-v1/lrolol_1xxx/data/lola_gdr/cylindrical/img/ |
| LRO LOLA DEM 4 ppd | Globe layer map (elevation, slope, water ice) | https://pds-geosciences.wustl.edu/lro/lro-l-lola-3-rdr-v1/lrolol_1xxx/data/lola_gdr/cylindrical/img/ |
| MIT Imbrium LOLA mirror | Fallback DEM download | https://imbrium.mit.edu/DATA/LOLA_GDR/CYLINDRICAL/IMG/ |
| Solar System Scope 8K Moon texture (CC BY 4.0) | Moon surface texture | https://www.solarsystemscope.com/textures/download/8k_moon.jpg |
| timeapi.io | Live UTC time sync | https://timeapi.io |

---

## Installation & Local Setup

### Prerequisites
- Python 3.11 or higher
- pip
- Git

### Steps

```bash
# 1. Clone the repository
git clone https://github.com/Victorhii/astropulse.git
cd astropulse

# 2. Create and activate a virtual environment
python -m venv venv
source venv/bin/activate        # macOS / Linux
venv\Scripts\activate           # Windows

# 3. Install dependencies
pip install -r requirements.txt

# 4. Run the Flask development server
python app.py
```

The app will be available at `http://localhost:5001`.

> **First run note:** `lunar_probe.py` will automatically download the four NASA SPICE kernels (~20 MB total) into a `kernels/` folder. This happens once and is cached for all subsequent runs. The 8K Moon texture is also downloaded and cached on the first request to `/api/lunar-texture-8k`.

### requirements.txt

```
Flask>=3.0
numpy>=1.24
scipy>=1.10
requests>=2.31
spiceypy>=6.0
```

---

## Deployment on Vercel

Astropulse is deployed on Vercel using a Python Flask serverless configuration.

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/time` | Synced UTC timestamp from timeapi.io |
| `GET` | `/api/lunar-texture-8k` | Cached 8K Moon texture (served with 1-year cache header) |
| `GET` | `/api/lunar-layer-map` | Cached LOLA elevation/slope/ice stability PNG |
| `POST` | `/compare_results` | Four-site comparison — returns rendered `results.html` |
| `GET` | `/api/lunar-sim-data` | Full simulation payload (ephemeris, DEM, horizon profile) |

### `/compare_results` payload example

```json
{
  "temperature_range": { "min_kelvin": 100.0, "max_kelvin": 350.0 },
  "site1": { "coordinate": "-85.0, 31.0", "date": "12/11/2026", "time": "14:30:00" },
  "site2": { "coordinate": "-89.5, 0.0",  "date": "12/11/2026", "time": "14:30:00" },
  "site3": { "coordinate": "26.13, 3.63", "date": "15/11/2026", "time": "08:00:00" },
  "site4": { "coordinate": "0.67, 23.47", "date": "15/11/2026", "time": "08:00:00" }
}
```

---

## Scientific Methods

| Component | Method / Reference |
|-----------|-------------------|
| Sun & Earth positions | NASA/NAIF SPICE kernels (`de421.bsp`, `moon_pa_de421_1900-2050.bpc`); valid 1901–2049 |
| Terrain elevation & slope | LRO LOLA 128 ppd DEM via HTTP Range requests (no full-file download) |
| Lunar eclipse fraction | Disc-overlap geometry (exact analytic formula) |
| Optical libration | Low-precision orbital elements (Meeus-style); accurate to ~0.1° |

---

## AI Disclosure

AI-assisted tools were used during development. See [`AI_DISCLOSURE.md`](./AI_DISCLOSURE.md) for the full declaration.

| Tool | Role |
|------|------|
| Claude (Anthropic) | Lunar data fetching and backend logic |
| Gemini (Google DeepMind) | UI layout and simulation frontend |

All AI-generated output was reviewed, tested, and validated by the team.

---

## Resources & Attributions

- **NASA/NAIF SPICE** — https://naif.jpl.nasa.gov
- **NASA PDS Geosciences Node (LOLA)** — https://pds-geosciences.wustl.edu
- **MIT Imbrium LOLA Mirror** — https://imbrium.mit.edu
- **JPL Solar System Dynamics** — https://ssd.jpl.nasa.gov
- **Solar System Scope Moon Texture (CC BY 4.0)** — https://www.solarsystemscope.com/textures/download/8k_moon.jpg
- **timeapi.io** — https://timeapi.io

---

## License

This project was created for the 2026 NASA Space Apps Challenge. All scientific data remains the property of their respective providers (NASA, JPL, MIT). Third-party libraries retain their original licences as listed above.
