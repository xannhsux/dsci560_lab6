# dsci560_lab6

## Overview
This project ingests oil well PDF reports into MySQL and exposes the data through a Flask API and Leaflet-powered web map. Docker Compose orchestrates the complete stack:

- `mysql`: relational database persisting well and stimulation details
- `pdf_parser`: ETL job that extracts information from PDFs into MySQL
- `backend`: Flask application providing REST endpoints for the web UI
- `nginx`: static web server (and reverse proxy) hosting the Leaflet map
- `phpmyadmin`: optional UI for inspecting database contents

## Quick Start
1. Ensure Docker and Docker Compose are installed.
2. Add PDF source documents into `./pdfs` (the directory is mounted read-only by the web server and parser).
3. Launch the MySQL database and supporting services:
   ```bash
   docker compose up -d mysql backend nginx
   ```
4. Populate the database from PDFs (run as needed when new files arrive):
   ```bash
   docker compose run --rm pdf_parser
   ```
5. Open the map UI at [http://localhost:8080](http://localhost:8080). The API is reachable at [http://localhost:8080/api/wells](http://localhost:8080/api/wells).

## API Endpoints
The Flask backend (proxied through Nginx) exposes:
- `GET /api/health` – service health check
- `GET /api/wells` – list of wells with coordinates and stimulation records
- `GET /api/wells/<api>` – detailed information for a specific API number

## Web Visualisation
The Leaflet interface (`web/frontend`) displays wells as push pins using latitude/longitude from the database. Selecting a marker reveals:
- PDF-derived metadata (operator, job type, SHL, datum)
- Stimulation data (formation, stages, volumes, pressure, etc.)
- Placeholders to integrate crawled insights or links to original PDFs

Use the sidebar to jump directly to any well and the map to explore geographically. Tiles come from OpenStreetMap; ensure outbound internet is available for map imagery.

## Validation Tips
- Check the backend with `curl http://localhost:8080/api/health`.
- Verify data presence through phpMyAdmin (`docker compose up -d phpmyadmin`, then browse http://localhost:8081).
- Review container logs with `docker compose logs -f backend` or `nginx` for troubleshooting.
