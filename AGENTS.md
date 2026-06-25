# AGENTS.md

## Cursor Cloud specific instructions

This repo has two parts:

- **Static frontend** (`index.html`): a Chinese-language ECharts dashboard ("全国保研院校热度地图"). It `fetch`es `data/schools.json` and `data/summer_camp_notices.json` at runtime, so it MUST be served over HTTP — opening it via `file://` breaks the `fetch` calls. A second standalone page lives at `phys-experiment-quiz/index.html`.
- **Python crawler** (`crawler/update_data.py`): refreshes the JSON in `data/` and is normally run by GitHub Actions (`.github/workflows/update.yml`), not in production. Deps are in `requirements.txt`.

Non-obvious notes:

- The frontend loads ECharts from `cdn.jsdelivr.net` and the China GeoJSON from `geo.datav.aliyun.com` at runtime; both must be network-reachable or the map won't render.
- Serve the site from the repo root (so `data/` is reachable): `python3 -m http.server 8000`, then open `http://localhost:8000/index.html`.
- The crawler is resilient: if scraping returns nothing it keeps existing + seed notices so the frontend never goes blank. Running it rewrites files under `data/`; discard those changes unless a data refresh is intended.
- There is no build step, no automated test suite, and no linter configured for this repo.
