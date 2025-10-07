"""Demo version of enrich_wells.py using simulated data.

Use this script for testing the enrichment workflow when drillingedge.com
is unavailable or for development purposes.

SYSTEM ARCHITECTURE:
====================

Backend Stack:
- Python 3.10 with Flask for REST API
- MySQL 8.0 for relational database storage
- SQLAlchemy ORM for database interactions
- Docker Compose for container orchestration

Frontend Stack:
- Vanilla JavaScript (ES6+) - No Node.js build process required
- Leaflet.js - Open-source JavaScript library for interactive maps
  * Tile-based mapping with OpenStreetMap data
  * Marker clustering and popup management
  * Responsive pan/zoom controls
- HTML5 + CSS3 for responsive UI
- Nginx web server for static file serving

Map Visualization:
- Leaflet renders well locations on interactive map using lat/lon coordinates
- Custom markers for each well with click handlers
- Sliding side panel displays detailed well information
- Real-time data fetched from Flask API endpoint (/api/wells)
- GeoJSON-compatible data structure for spatial rendering

Data Flow:
1. PDF Parser → MySQL (well metadata + stimulation data)
2. Web Scraper → MySQL (enriches with coordinates + production stats)
3. Flask API → JSON serialization of database records
4. Nginx serves static frontend files (HTML/CSS/JS)
5. Leaflet.js renders map markers using coordinate data from API
6. User clicks marker → Side panel displays well details
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Optional

if __package__ in (None, ""):
    sys.path.append(str(Path(__file__).resolve().parent))
    from db_utils import Well, get_session
    from web_scraper_demo import scrape_well_data_demo
else:
    from .db_utils import Well, get_session
    from .web_scraper_demo import scrape_well_data_demo


logger = logging.getLogger(__name__)


def enrich_well(well: Well, force: bool = False) -> bool:
    """Enrich a single well with demo scraped data."""
    if not force and well.well_status and well.well_status != 'N/A':
        logger.info(f"Skipping well {well.api} - already enriched")
        return True

    logger.info(f"Enriching well {well.api} / {well.well_name} with DEMO data")

    # Get demo data
    scraped_data = scrape_well_data_demo(well.api or '', well.well_name or '')

    # Update the well object
    well.well_status = scraped_data.get('well_status', 'N/A')
    well.well_type = scraped_data.get('well_type', 'N/A')
    well.closest_city = scraped_data.get('closest_city', 'N/A')
    well.barrels_oil_produced = scraped_data.get('barrels_oil_produced', 0.0)
    well.gas_produced = scraped_data.get('gas_produced', 0.0)

    logger.info(f"Enriched well {well.api}: status={well.well_status}, "
               f"type={well.well_type}, city={well.closest_city}")

    return True


def enrich_all_wells(limit: Optional[int] = None, api_filter: Optional[str] = None,
                     force: bool = False) -> None:
    """Enrich all wells in the database with demo scraped data."""
    session = get_session()

    try:
        query = session.query(Well)

        if api_filter:
            query = query.filter(Well.api == api_filter)
        else:
            query = query.order_by(Well.id.asc())

        if limit:
            query = query.limit(limit)

        wells = query.all()
        total = len(wells)

        if total == 0:
            logger.warning("No wells found to enrich")
            return

        logger.info(f"Found {total} well(s) to enrich with DEMO data")

        success_count = 0
        error_count = 0

        for i, well in enumerate(wells, start=1):
            logger.info(f"Processing well {i}/{total}: {well.api}")

            try:
                if enrich_well(well, force=force):
                    session.commit()
                    success_count += 1
                else:
                    error_count += 1
            except Exception as exc:
                logger.error(f"Error enriching well {well.api}: {exc}")
                session.rollback()
                error_count += 1

        logger.info(f"DEMO Enrichment complete: {success_count} successful, {error_count} errors")

    finally:
        session.close()


def main():
    """Main entry point for the demo enrichment script."""
    parser = argparse.ArgumentParser(
        description="Enrich well data with DEMO/SIMULATED information (for testing)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
DOCKER COMMANDS:
================
This system uses Docker Compose to orchestrate multiple containers.

Starting the System:
  docker-compose up -d
    - Starts all services in detached mode (background)
    - Services: mysql, backend (Flask API), nginx (web server), phpmyadmin

Running the Data Pipeline:
  docker-compose run --rm data_pipeline
    - Executes the complete ETL pipeline in a temporary container
    - --rm automatically removes container after completion
    - Runs 3 steps sequentially:
      1. PDF Parser: Extracts data from 65 PDFs using PyPDF2 + Tesseract OCR
      2. Web Scraper: Enriches wells with coordinates from drillingedge.com
      3. Cleanup: Removes wells with invalid API numbers (non-ND wells)
    - Takes ~5-10 minutes for full processing

Viewing Logs:
  docker-compose logs -f data_pipeline
    - Shows real-time output from pipeline execution
    - Press Ctrl+C to exit log view

Stopping the System:
  docker-compose down
    - Stops and removes all containers
    - Preserves MySQL data in named volume

  docker-compose down -v
    - Stops containers AND removes volumes (clears database)

Accessing Services:
  docker-compose exec mysql mysql -u oil_user -poil_pass -D oil_wells
    - Opens MySQL CLI inside running container
    - Useful for running SQL queries directly

Individual Service Control:
  docker-compose up -d mysql phpmyadmin backend nginx
    - Start specific services only

  docker-compose restart backend
    - Restart a single service

Examples:
  # Enrich all wells with demo data
  python enrich_wells_demo.py

  # Enrich first 5 wells (for testing)
  python enrich_wells_demo.py --limit 5

  # Enrich a specific well
  python enrich_wells_demo.py --api 33-053-06057
        """
    )

    parser.add_argument('--limit', type=int, default=None,
                       help='Maximum number of wells to process')
    parser.add_argument('--api', type=str, default=None,
                       help='Only process the well with this API number')
    parser.add_argument('--force', action='store_true',
                       help='Re-enrich wells even if they already have scraped data')
    parser.add_argument('--verbose', '-v', action='store_true',
                       help='Enable verbose logging')

    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(levelname)s - %(name)s: %(message)s"
    )

    logger.info("Starting DEMO well enrichment process")
    enrich_all_wells(limit=args.limit, api_filter=args.api, force=args.force)
    logger.info("DEMO well enrichment process complete")


if __name__ == "__main__":
    main()
