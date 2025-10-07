"""Test web scraper with 5 real wells from the database"""

import sys
sys.path.insert(0, 'src')

from db_utils import Well, get_session
from web_scraper import scrape_well_data
import logging

# Set up logging to see what's happening
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s: %(message)s',
    datefmt='%H:%M:%S'
)

logger = logging.getLogger(__name__)

def test_scraper_with_database_wells():
    """Test scraping with 5 wells from the database."""

    logger.info("Connecting to database...")
    session = get_session()

    try:
        # Get first 5 wells
        wells = session.query(Well).limit(5).all()

        if not wells:
            logger.error("No wells found in database!")
            logger.error("Run pdf_parser first: docker compose run --rm pdf_parser")
            return

        logger.info(f"Found {len(wells)} wells to test\n")
        logger.info("=" * 80)

        for i, well in enumerate(wells, 1):
            logger.info(f"\nTEST {i}/5: {well.well_name}")
            logger.info("-" * 80)
            logger.info(f"  API: {well.api}")
            logger.info(f"  Operator: {well.operator}")
            logger.info(f"  Current Status: {well.well_status or 'Not enriched'}")

            # Try to scrape data
            logger.info(f"\n  Attempting to scrape from drillingedge.com...")

            try:
                scraped_data = scrape_well_data(well.api or '', well.well_name or '')

                logger.info(f"\n  RESULTS:")
                logger.info(f"    Well Status: {scraped_data.get('well_status')}")
                logger.info(f"    Well Type: {scraped_data.get('well_type')}")
                logger.info(f"    Closest City: {scraped_data.get('closest_city')}")
                logger.info(f"    Oil Produced: {scraped_data.get('barrels_oil_produced')} barrels")
                logger.info(f"    Gas Produced: {scraped_data.get('gas_produced')} MCF")

                # Check if we got any real data
                has_data = (
                    scraped_data.get('well_status') not in (None, 'N/A') or
                    scraped_data.get('well_type') not in (None, 'N/A') or
                    scraped_data.get('closest_city') not in (None, 'N/A') or
                    scraped_data.get('barrels_oil_produced', 0) > 0 or
                    scraped_data.get('gas_produced', 0) > 0
                )

                if has_data:
                    logger.info(f"\n  ✅ SUCCESS - Got real data!")
                else:
                    logger.info(f"\n  ⚠️  No data retrieved (defaults returned)")

            except Exception as e:
                logger.error(f"\n  ❌ ERROR: {e}")

            logger.info("\n" + "=" * 80)

        logger.info("\n\nTEST SUMMARY:")
        logger.info("=" * 80)
        logger.info("If you see mostly 'No data retrieved' or timeouts:")
        logger.info("  → This is EXPECTED due to website anti-scraping measures")
        logger.info("  → Use demo_scraper for reliable results:")
        logger.info("    docker compose run --rm demo_scraper")
        logger.info("\nIf you got real data:")
        logger.info("  → Great! The real scraper worked!")
        logger.info("  → You can use: docker compose run --rm web_scraper")

    finally:
        session.close()

if __name__ == "__main__":
    test_scraper_with_database_wells()
