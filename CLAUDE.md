# Quiet Places — Project Context

## Vision
"Quiet Places" is a research art project by Mark (faculty, School of Visual Arts, NYC). The goal is a "Map of Quiet Places" (MQP): a dynamic, web-based map that helps locate the quietest places on Earth — meaning no audible anthropogenic sound (no airplanes, motor vehicles, trains, weapons, or industry). In practical terms: no nearby roads or active railroad tracks, and no airplanes flying within 20 miles.

The map should:
- Be pannable and zoomable like Google Maps
- Show roads, railroads, towns, national/regional boundaries
- Show red lines where airplanes have traveled
- Dense flight corridors appear as webs of lines that separate into individual lines on zoom
- Lines are bright red when new, darkening as they age
- Update periodically with new flight data

## Acoustic design notes
- Commercial jets at ~35,000ft: audible ~20-30 miles
- A plane traveling 500mph crosses a 40-mile quiet zone (20-mile radius) in under 5 minutes
- 3-minute sampling interval was chosen so consecutive positions reliably overlap within a 20-mile acoustic radius (~25 miles traveled per interval)
- Small planes and helicopters fly lower and are louder close up; altitude-dependent rendering is a future enhancement

## Live map
https://foilsafe101.github.io/quiet-places/

## Repo
https://github.com/foilsafe101/quiet-places
- Branch: main
- GitHub Pages: served from /docs folder
- Auth: Personal access token embedded in remote URL (set via git remote set-url)

## Users / machines
- `foil` — non-admin macOS user, runs Claude Code (current user)
- `markustribus` — admin macOS user, owns machine
- quiet-places folder: /Users/markustribus/quiet-places (chowned to foil Apr 18)

## Python environment
- .venv at ~/quiet-places/.venv
- Run scripts with: .venv/bin/python <script>
- pyopensky 2.16 installed

## OpenSky credentials
- Account: mtribe@sva.edu (SVA faculty account — approved for Trino access)
- Config: ~/Library/Application Support/pyopensky/settings.conf (markustribus home)
- Auth: falls back to browser OAuth2 per query (credentials not picked up by Trino client)
- Background watcher auto-closes OAuth Chrome tabs and refocuses Claude (/tmp/close_oauth_windows.sh, PID 55107)
- Martin Strohmeier (OpenSky) approved access and said: "just go ahead and figure out what works. The system will largely limit the damage you can do."

## Scripts

### query_opensky.py (OLD — dot-based)
- Queries global flight positions (single points) from 2019–present
- Samples one hour every 3 days, all 24 hours per sampled day, rotating minute offsets
- Progress tracked in query_progress.json
- Output: docs/flights.json
- Status: Batch 1+2 complete (~400 queries, ~400,321 positions)
- Issue: connecting same aircraft across weeks produces spurious long lines — this approach is superseded

### query_opensky_paths.py (NEW — path-based, preferred)
- Queries connected flight paths for all of 2025 (one week per month, all 7 days)
- 3-minute sampling intervals × 9 land regions × 480 slots/day × 84 days
- Total: 362,880 query slots (~605 hours runtime at 6s/slot)
- Progress tracked in query_progress_paths.json
- Output: docs/flights.json (same file, appends tracks)
- Key design: accumulates positions per aircraft per day, connects consecutive positions within 10-min gaps into real multi-point path segments
- Fixed: uses opensky.query() not opensky.rawquery()
- Status: Batch 1 in progress (~200 slots)

### Land regions (9 bounding boxes, excludes oceans)
North America, Europe, Middle East, South Asia, East Asia, Southeast Asia, Africa, South America, Australia

## Map frontend (docs/index.html)
- Leaflet.js with CartoDB Voyager tiles (warm beige, Google Maps-like)
- Flight paths colored by age: bright red (recent) → near-black (old)
- Line-connecting logic: groups positions by ICAO24, connects within 30-day gap
- Toggle button to show/hide flight paths
- Loading overlay with spinner
- Status bar shows track count and last updated date
- The mqp/ folder (from earlier Desktop session) has a polished alternative version

## Pending improvements
- Layer switcher (like Gaia GPS): Default/Voyager, Land Cover (ESA WorldCover), Terrain, Satellite
- Fix spurious long lines (same aircraft connected across weeks via old dot-based data)
- Altitude-dependent rendering (low-flying aircraft have smaller audible radius)
- "Quiet zone" overlay: highlight areas with zero flight activity

## Workflow for batches
1. Run batch: .venv/bin/python query_opensky_paths.py --batch 200
2. Commit & push: git add docs/flights.json query_progress_paths.json && git commit -m "Add paths batch N" && git push
3. Pause, wait for user to say "go"
4. Repeat

## Background processes
- OAuth tab closer: /tmp/close_oauth_windows.sh (PID 55107) — closes OAuth Chrome tabs and refocuses Claude every 3s
- Batch monitor: watching output for completion/errors automatically

## GitHub token
Embedded in remote URL — no need to enter credentials manually.
