"""Script to enrich well data with information from drillingedge.com.

This script iterates through all wells in the database and enriches them with
additional information scraped from drillingedge.com, including well status,
well type, closest city, and production data.

Usage:
    python enrich_wells.py [options]

Options:
    --limit N       Only process the first N wells (useful for testing)
    --api API       Only process the well with the given API number
    --force         Re-scrape wells even if they already have scraped data

Running as module:
    python -m src.enrich_wells
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
    from web_scraper import scrape_well_data
else:
    from .db_utils import Well, get_session
    from .web_scraper import scrape_well_data


logger = logging.getLogger(__name__)


def needs_coordinate_refresh(well: Well) -> bool:
    """Return True if the well has obviously invalid coordinates."""

    def _is_zero(value: Optional[float]) -> bool:
        return value is not None and abs(value) < 1e-6

    return _is_zero(well.latitude) or _is_zero(well.longitude)


def enrich_well(well: Well, force: bool = False) -> bool:
    """Enrich a single well with scraped data.

    Args:
        well: The Well object to enrich
        force: If True, re-scrape even if data already exists

    Returns:
        True if enrichment was successful, False otherwise
    """
    # Skip if already enriched (unless force is True)
    if not force and well.well_status and well.well_status != 'N/A':
        if needs_coordinate_refresh(well):
            logger.info(
                f"Re-scraping well {well.api} despite existing enrichment because coordinates are zero"
            )
        else:
            logger.info(f"Skipping well {well.api} - already enriched")
            return True

    logger.info(f"Enriching well {well.api} / {well.well_name}")

    # Scrape data
    scraped_data = scrape_well_data(well.api or '', well.well_name or '', well.county_state or '')

    # Update well name if we got a better one from the website
    if scraped_data.get('well_name'):
        well.well_name = scraped_data.get('well_name')

    # Update operator if we got one from the website
    if scraped_data.get('operator'):
        well.operator = scraped_data.get('operator')

    # Update county/state if we got one from the website
    if scraped_data.get('county_state'):
        well.county_state = scraped_data.get('county_state')

    # Update the well object with production data
    well.well_status = scraped_data.get('well_status', 'N/A')
    well.well_type = scraped_data.get('well_type', 'N/A')
    well.closest_city = scraped_data.get('closest_city', 'N/A')
    well.barrels_oil_produced = scraped_data.get('barrels_oil_produced', 0.0)
    well.gas_produced = scraped_data.get('gas_produced', 0.0)

    # Always update lat/lon if we got better data from the website
    if scraped_data.get('latitude') is not None:
        well.latitude = scraped_data.get('latitude')
    if scraped_data.get('longitude') is not None:
        well.longitude = scraped_data.get('longitude')

    logger.info(f"Enriched well {well.api}: status={well.well_status}, "
               f"type={well.well_type}, city={well.closest_city}")

    return True


def enrich_all_wells(limit: Optional[int] = None, api_filter: Optional[str] = None,
                     force: bool = False) -> None:
    """Enrich all wells in the database with scraped data.

    Args:
        limit: Maximum number of wells to process
        api_filter: If provided, only process the well with this API number
        force: If True, re-scrape even if data already exists
    """
    session = get_session()

    try:
        # Build query
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

        logger.info(f"Found {total} well(s) to enrich")

        success_count = 0
        error_count = 0

        for i, well in enumerate(wells, start=1):
            logger.info(f"Processing well {i}/{total}: {well.api}")

            try:
                coordinate_retry = needs_coordinate_refresh(well)
                if coordinate_retry and not force:
                    logger.info(
                        f"Forcing scrape for well {well.api} because latitude/longitude is exactly zero"
                    )

                if enrich_well(well, force=force or coordinate_retry):
                    session.commit()
                    success_count += 1
                else:
                    error_count += 1
            except Exception as exc:
                logger.error(f"Error enriching well {well.api}: {exc}")
                session.rollback()
                error_count += 1

        logger.info(f"Enrichment complete: {success_count} successful, {error_count} errors")

    finally:
        session.close()


def main():
    """Main entry point for the enrichment script."""
    parser = argparse.ArgumentParser(
        description="Enrich well data with information from drillingedge.com",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Enrich all wells
  python enrich_wells.py

  # Enrich first 5 wells (for testing)
  python enrich_wells.py --limit 5

  # Enrich a specific well
  python enrich_wells.py --api 33-053-06057

  # Force re-scraping of all wells
  python enrich_wells.py --force
        """
    )

    parser.add_argument('--limit', type=int, default=None,
                       help='Maximum number of wells to process')
    parser.add_argument('--api', type=str, default=None,
                       help='Only process the well with this API number')
    parser.add_argument('--force', action='store_true',
                       help='Re-scrape wells even if they already have scraped data')
    parser.add_argument('--verbose', '-v', action='store_true',
                       help='Enable verbose logging')

    args = parser.parse_args()

    # Set up logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(levelname)s - %(name)s: %(message)s"
    )

    # Run enrichment
    logger.info("Starting well enrichment process")
    enrich_all_wells(limit=args.limit, api_filter=args.api, force=args.force)
    logger.info("Well enrichment process complete")


if __name__ == "__main__":
    main()
