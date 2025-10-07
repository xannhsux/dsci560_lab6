# dsci560_lab6

## Overview
This lab implements an end-to-end data pipeline for North Dakota oil well completion reports. The system ingests PDF reports, extracts structured information using OCR and regex parsing, enriches the data through web scraping, stores it in MySQL, and visualizes the results through an interactive web map. The complete workflow runs inside Docker Compose for reproducible deployment.

### What the project can do
- **OCR Processing**: Extract text from PDF completion reports using PyPDF2 and Tesseract OCR with intelligent caching and quality validation.
- **Regex Parsing**: Parse semi-structured data (well names, API numbers, coordinates, stimulation parameters) using pattern matching with error correction.
- **Web Scraping**: Enrich well data by scraping drillingedge.com for well status, type, closest city, and production metrics.
- **Database Management**: Persist extracted and scraped data in MySQL using SQLAlchemy models with integrity validation.
- **REST API**: Serve JSON endpoints (`/api/wells`, `/api/wells/<api>`, `/api/health`) for downstream applications.
- **Interactive Map**: Visualize wells on a Leaflet map with clickable markers showing metadata, stimulation details, and production data.
- **Database Inspection**: Provide phpMyAdmin access for ad-hoc database queries and verification.

## Repository guide
| Path | Description |
| --- | --- |
| `docker-compose.yml` | Orchestrates all services (MySQL, pdf parser, web scraper, Flask backend, Nginx frontend, phpMyAdmin). |
| `Dockerfile` | Base Python image used by parser, scraper, and backend containers (installs OCR utilities and Python deps). |
| `requirements.txt` | Python dependencies shared by parser, scraper, and web backend. |
| `src/db_utils.py` | SQLAlchemy models (`Well`, `StimulationData`) and session helpers. |
| `src/pdf_parser.py` | End-to-end PDF extraction pipeline (text/OCR + regex parsing + database upsert). Runnable as a script. |
| `src/web_scraper.py` | Web scraping module for enriching wells with data from drillingedge.com. |
| `src/enrich_wells.py` | Script to iterate through database wells and enrich with scraped data. |
| `src/webapp/app.py` | Flask application exposing REST endpoints for wells and stimulations. |
| `src/webapp/__init__.py` | Package initialiser for the Flask app. |
| `web/frontend/index.html` | Leaflet map page served by Nginx. |
| `web/frontend/styles.css` | Styling for the web interface (sidebar + popup layout). |
| `web/frontend/app.js` | Front-end logic: fetches API data, renders well list, creates map markers/popups. |
| `nginx/default.conf` | Nginx site config: serves static assets and proxies `/api/` calls to Flask. |
| `pdfs/` | Drop raw PDF reports here before running the parser (mounted read-only into containers). |
| `sql/` | Optional place for MySQL init scripts (mounted automatically if present). |
| `WEB_SCRAPER_README.md` | Detailed documentation for the web scraping component (Part 4). |

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
4. **Enrich with web-scraped data** (Part 4)
   ```bash
   docker compose run --rm web_scraper
   ```
   This scrapes additional well information from drillingedge.com (well status, type, closest city, and production data).
5. **Verify endpoints**
   ```bash
   curl http://localhost:8080/api/health
   curl http://localhost:8080/api/wells | jq '.[0]'
   ```
6. **Open the web map** – visit [http://localhost:8080](http://localhost:8080).
7. **(Optional) phpMyAdmin**
   ```bash
   docker compose up -d phpmyadmin
   ```
   Browse to [http://localhost:8081](http://localhost:8081) and log in with the credentials from `docker-compose.yml`.

## Useful commands
- Stop services: `docker compose down`
- Tail logs: `docker compose logs -f backend` (or `nginx`, `mysql`, etc.)
- Re-run parser after adding PDFs: `docker compose run --rm pdf_parser`
- Enrich wells with scraped data: `docker compose run --rm web_scraper`
- Re-scrape all wells (force): `docker compose run --rm web_scraper python src/enrich_wells.py --force`
- Test scraper with limited wells: `docker compose run --rm web_scraper python src/enrich_wells.py --limit 5`
- Restart frontend when tweaking HTML/JS/CSS: `docker compose restart nginx`
- Clean MySQL volumes (destructive!): `docker compose down -v`

## Troubleshooting
- **Empty `/api/wells` response**: ensure PDFs exist in `./pdfs/` and rerun the parser. Check parser stdout for OCR errors.
- **Markers missing on the map**: entries without valid decimal latitude/longitude appear only in the sidebar. Update source data or database values and refresh.
- **Leaflet tiles not loading**: confirm internet access to `tile.openstreetmap.org`; corporate proxies may block the requests.
- **Port conflicts**: adjust host ports in `docker-compose.yml` (e.g., change `8080:80`) if other services already bind those ports.

With the stack running, you can extend the API, enrich popups with crawler results, or add filters/search to the map as next steps.

---

## Data Pipeline Architecture

### 1. PDF OCR Process

The OCR pipeline ([src/pdf_parser.py](src/pdf_parser.py)) extracts text from PDF completion reports using a hybrid approach:

#### Extraction Methods
- **PyPDF2 Text Extraction**: Fast extraction of embedded text from native PDFs
- **Tesseract OCR**: Image-based OCR for scanned documents or pages with poor text extraction
- **Intelligent Selection**: System automatically chooses the best method per page based on content quality

#### Quality Validation & Retry Logic
The parser validates extraction quality using domain-specific rules:
- **API Number Validation**: Checks for valid North Dakota format (`33-XXX-XXXXX`) and detects OCR errors (prefix digits, wrong length)
- **Operator Name Validation**: Identifies OCR gibberish patterns and incomplete names, ensures valid company markers (LLC, Inc, Corporation)
- **County Validation**: Verifies North Dakota counties and catches common OCR errors (dates mistaken for counties)

When validation fails, the system:
1. Identifies pages likely containing errors
2. Re-extracts those pages using Tesseract OCR with image preprocessing (grayscale, contrast enhancement, sharpening)
3. Re-parses the improved text

#### Caching System
- **Cache Directory**: `ocr_cache/` stores extracted text as JSON files
- **Integrity Checking**: MD5 hash and file size verification prevent stale cache hits
- **Performance**: Dramatically reduces processing time for unchanged PDFs
- **Per-Page Tracking**: Stores extraction method (PyPDF2 vs Tesseract) for each page

#### OCR Manifest
The `ocr_targets.json` file marks specific pages that always require OCR (discovered through iterative quality improvement).

### 2. Regex Pattern Matching

The parser ([src/pdf_parser.py](src/pdf_parser.py)) uses comprehensive regex patterns to extract structured data:

#### Well-Level Fields
- **API Number**: Multiple patterns for format variations (`33-053-06223`, with/without leading digits)
- **Well Name**: Handles reverse order (value above label), multi-line formats
- **Operator**: Matches company names with common suffixes (LLC, Inc, Corporation), includes OCR error correction for known operators
- **County/State**: Direct ND county patterns, handles abbreviated formats
- **Coordinates**: Supports both DMS format (`N 48° 1' 29"`) and decimal degrees, matches "value above label" layouts
- **Datum**: Extracts coordinate system (NAD 83, NAVD 88, WGS 84)

#### Stimulation-Level Fields
- **Date Stimulated**: Various date formats
- **Formation**: Bakken, Three Forks, Middle Bakken variants
- **Depth**: Top/bottom footage with multi-line support
- **Treatment Type**: Slickwater, Acid, Hybrid, Gel, Foam
- **Stages**: Number of stimulation stages
- **Volume**: With units (bbls, gal, m³)
- **Proppant**: Pounds of proppant used
- **Pressure/Rate**: Maximum treatment pressure and rate

#### Error Correction
- **String Cleaning**: Removes HTML entities, non-printable characters, normalizes whitespace
- **OCR Error Patterns**: Corrects common OCR misreads (O→0, I→1, etc.)
- **Validation**: Treatment types validated against known values, invalid entries marked as "N/A"

### 3. Web Scraping Process

The web scraper ([src/web_scraper.py](src/web_scraper.py)) enriches well data from drillingedge.com:

#### Data Collection
- **Well Status**: Active, Inactive, Permitted, Drilling
- **Well Type**: Oil, Gas, Injection, Water Disposal
- **Closest City**: Nearest municipality to well location
- **Production Data**: Total barrels of oil produced, gas produced (MCF)

#### Scraping Strategy
1. **URL Construction**: Builds search URL using API number, well name, and county/state
2. **HTML Parsing**: Uses BeautifulSoup to parse well detail pages
3. **Field Extraction**: Locates data in definition lists, tables, and labeled sections
4. **Error Handling**: Gracefully handles missing fields, network errors, parsing failures

#### Quality Assurance
- **Coordinate Validation**: Only processes wells with valid decimal coordinates
- **Production Parsing**: Handles formatted numbers with commas and decimal points
- **Rate Limiting**: Includes delays between requests to avoid overloading the server
- **Selective Updates**: Only scrapes wells missing enrichment data (unless `--force` flag used)

#### Enrichment Script
The [src/enrich_wells.py](src/enrich_wells.py) script orchestrates web scraping:
```bash
# Enrich all wells missing scraped data
docker compose run --rm web_scraper

# Force re-scrape of all wells
docker compose run --rm web_scraper python src/enrich_wells.py --force

# Test with limited wells
docker compose run --rm web_scraper python src/enrich_wells.py --limit 5
```

### 4. Database Update & Error Checking

After web scraping, the system updates the MySQL database with enriched data:

#### Database Schema
**Wells Table** ([src/db_utils.py](src/db_utils.py)):
- **PDF-Extracted Fields**: api, well_name, operator, county_state, latitude, longitude, datum, shl
- **Web-Scraped Fields**: well_status, well_type, closest_city, barrels_oil_produced, gas_produced

**Stimulation Table**:
- Links to wells via foreign key
- Stores treatment details: date, formation, depth range, stages, volume, proppant, pressure, rate

#### Error Checking Mechanisms
1. **Coordinate Validation**: Ensures latitude/longitude are valid decimal numbers before scraping
2. **Production Data Validation**: Verifies numeric fields parse correctly
3. **Database Integrity**: Foreign key constraints ensure stimulation data links to valid wells
4. **Transaction Rollback**: Failed updates don't corrupt database state
5. **Logging**: Detailed logs track extraction quality, scraping success/failure, database operations

#### Data Quality Reports
The system generates quality reports tracking:
- Fields missing data (N/A or NULL)
- Extraction method used per well
- Validation failures and retry attempts
- Scraping success rates

### 5. Web Application

The web interface provides interactive access to well data:

#### Backend ([src/webapp/app.py](src/webapp/app.py))
**REST API Endpoints**:
- `GET /api/health`: Health check endpoint
- `GET /api/wells`: Returns all wells with stimulation data (JSON array)
- `GET /api/wells/<api>`: Returns single well by API number

**Features**:
- SQLAlchemy ORM for database queries
- JSON serialization of well and stimulation objects
- CORS headers for cross-origin requests
- Error handling with appropriate HTTP status codes

#### Frontend ([web/frontend/](web/frontend/))

**Map Interface** ([index.html](web/frontend/index.html), [app.js](web/frontend/app.js)):
- **Interactive Map**: Leaflet.js map centered on North Dakota
- **Well Markers**: Clickable pins at well coordinates with custom icons
- **Sidebar List**: Scrollable list of all wells (click to pan map to well)
- **Popup Display**: Rich information cards showing:
  - Well identification (name, API, operator)
  - Location (county, coordinates, SHL, closest city)
  - Well status and type (from web scraping)
  - Production metrics (barrels of oil, gas produced)
  - Stimulation details (date, formation, stages, proppant, treatment type)

**User Interactions**:
1. **Click well in sidebar**: Map pans to well location and opens popup
2. **Click map marker**: Opens popup with well details
3. **Browse list**: Scroll through all wells alphabetically
4. **View details**: Each popup shows comprehensive well information

**Styling** ([styles.css](web/frontend/styles.css)):
- Responsive layout with fixed sidebar and full-screen map
- Clean, readable popup design with labeled sections
- Hover effects and visual feedback
- Mobile-friendly responsive design

#### Architecture
```
User Browser
    ↓
Nginx (Port 8080)
    ├─→ Static files (HTML, CSS, JS) from /usr/share/nginx/html
    └─→ API requests (/api/*) proxied to Flask backend (Port 5000)
            ↓
        Flask App
            ↓
        MySQL Database (Port 3306)
```

---

## Quick Start Guide

1. **Clone repository** and navigate to project directory
2. **Add PDFs** to `./pdfs/` directory
3. **Start services**: `docker compose up -d mysql backend nginx`
4. **Run OCR parser**: `docker compose run --rm pdf_parser`
5. **Enrich with web data**: `docker compose run --rm web_scraper`
6. **Open web app**: Visit [http://localhost:8080](http://localhost:8080)
7. **Inspect database**: `docker compose up -d phpmyadmin` then visit [http://localhost:8081](http://localhost:8081)

---

## Development Notes

### Adding New PDFs
1. Copy PDFs to `./pdfs/` directory
2. Run parser: `docker compose run --rm pdf_parser`
3. Check logs for extraction quality
4. Run enrichment: `docker compose run --rm web_scraper`
5. Refresh web interface

### Improving Regex Patterns
Edit patterns in [src/pdf_parser.py](src/pdf_parser.py):
- Add patterns to `WELL_PATTERNS` or `STIM_PATTERNS` dictionaries
- Test with sample PDFs
- Check extraction quality in logs and database

### Extending Web Scraper
Modify [src/web_scraper.py](src/web_scraper.py):
- Add new fields to `scrape_well_data()` function
- Update database schema in [src/db_utils.py](src/db_utils.py)
- Migrate database: `docker compose down -v && docker compose up -d mysql`

### Customizing Web Interface
- Edit HTML structure: [web/frontend/index.html](web/frontend/index.html)
- Modify styling: [web/frontend/styles.css](web/frontend/styles.css)
- Update JavaScript logic: [web/frontend/app.js](web/frontend/app.js)
- Restart nginx: `docker compose restart nginx`
