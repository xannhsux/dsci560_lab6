# dsci560_lab6

## Overview
This lab ingests oil well PDF reports, extracts structured information, loads it into MySQL, and publishes the data through a Flask REST API and a Leaflet-based web map. The complete workflow runs inside Docker Compose so you can reproduce the pipeline end-to-end with a few commands.

### What the project can do
- OCR and parse semi-structured PDF completion reports into well and stimulation tables.
- Persist the extracted data in MySQL using SQLAlchemy models.
- Serve JSON endpoints (`/api/wells`, `/api/wells/<api>`, `/api/health`) for downstream use.
- Visualise wells on a map with interactive popups summarising metadata and stimulation metrics.
- Provide phpMyAdmin access for ad-hoc inspection of the database.

## Repository guide
| Path | Description |
| --- | --- |
| `docker-compose.yml` | Orchestrates all services (MySQL, pdf parser, Flask backend, Nginx frontend, phpMyAdmin). |
| `Dockerfile` | Base Python image used by both the parser and backend containers (installs OCR utilities and Python deps). |
| `requirements.txt` | Python dependencies shared by parser and web backend. |
| `src/db_utils.py` | SQLAlchemy models (`Well`, `StimulationData`) and session helpers. |
| `src/pdf_parser.py` | End-to-end PDF extraction pipeline (text/OCR + regex parsing + database upsert). Runnable as a script. |
| `src/webapp/app.py` | Flask application exposing REST endpoints for wells and stimulations. |
| `src/webapp/__init__.py` | Package initialiser for the Flask app. |
| `web/frontend/index.html` | Leaflet map page served by Nginx. |
| `web/frontend/styles.css` | Styling for the web interface (sidebar + popup layout). |
| `web/frontend/app.js` | Front-end logic: fetches API data, renders well list, creates map markers/popups. |
| `nginx/default.conf` | Nginx site config: serves static assets and proxies `/api/` calls to Flask. |
| `pdfs/` | Drop raw PDF reports here before running the parser (mounted read-only into containers). |
| `sql/` | Optional place for MySQL init scripts (mounted automatically if present). |

## Environment setup
The stack is containerised—only Docker and Docker Compose are required on the host. If you prefer local Python tooling for development, create a venv and install `requirements.txt`, but the standard workflow uses containers.

1. Install Docker Desktop (includes Compose v2).
2. Clone the repository and switch into the project directory.
3. (Optional) Set environment variables in a `.env` file if you need to override database credentials; defaults are baked into `docker-compose.yml`.

## Running the project
1. **Start core services**
   ```bash
   docker compose up -d mysql backend nginx
   ```
2. **Load PDFs** – copy your input documents into `./pdfs/`.
3. **Parse and load data**
   ```bash
   docker compose run --rm pdf_parser
   ```
4. **Verify endpoints**
   ```bash
   curl http://localhost:8080/api/health
   curl http://localhost:8080/api/wells | jq '.[0]'
   ```
5. **Open the web map** – visit [http://localhost:8080](http://localhost:8080).
6. **(Optional) phpMyAdmin**
   ```bash
   docker compose up -d phpmyadmin
   ```
   Browse to [http://localhost:8081](http://localhost:8081) and log in with the credentials from `docker-compose.yml`.

## Useful commands
- Stop services: `docker compose down`
- Tail logs: `docker compose logs -f backend` (or `nginx`, `mysql`, etc.)
- Re-run parser after adding PDFs: `docker compose run --rm pdf_parser`
- Restart frontend when tweaking HTML/JS/CSS: `docker compose restart nginx`
- Clean MySQL volumes (destructive!): `docker compose down -v`

## Troubleshooting
- **Empty `/api/wells` response**: ensure PDFs exist in `./pdfs/` and rerun the parser. Check parser stdout for OCR errors.
- **Markers missing on the map**: entries without valid decimal latitude/longitude appear only in the sidebar. Update source data or database values and refresh.
- **Leaflet tiles not loading**: confirm internet access to `tile.openstreetmap.org`; corporate proxies may block the requests.
- **Port conflicts**: adjust host ports in `docker-compose.yml` (e.g., change `8080:80`) if other services already bind those ports.

With the stack running, you can extend the API, enrich popups with crawler results, or add filters/search to the map as next steps.
