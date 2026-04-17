# Quiet Places — Map of Quiet Places (MQP)

A dynamic web map showing where on Earth you can stand without hearing any human-made sound: no airplanes, roads, trains, or industry.

## How it works

1. `fetch_flights.py` runs nightly (via GitHub Actions) and fetches real flight tracks from the OpenSky Network API
2. Tracks are appended to `docs/flights.json` with timestamps, then committed back to the repo
3. GitHub Pages serves `docs/index.html` — a Leaflet map that loads `flights.json` and draws each track colored by age (bright red = recent, near-black = old)

## Setup

### 1. Enable GitHub Pages
In your repo settings → Pages → Source: Deploy from branch → branch: main, folder: /docs

### 2. Trigger the first data fetch
Actions tab → "Fetch nightly flight data" → Run workflow

### 3. OpenSky Research Access
For full historical dataset (2013–present): https://opensky-network.org/data/impala

## File structure

    quiet-places/
    ├── fetch_flights.py
    ├── requirements.txt
    ├── .github/workflows/fetch-nightly.yml
    └── docs/
        ├── index.html
        └── flights.json

## License
Research / non-commercial use. Flight data © OpenSky Network contributors.
