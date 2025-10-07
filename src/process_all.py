#!/usr/bin/env python3
"""
Combined script to run PDF parsing and web scraping in sequence.
This ensures the database is fully populated before the web app starts.
"""
import sys
import logging
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(name)s: %(message)s'
)
logger = logging.getLogger(__name__)


def run_pdf_parser():
    """Run the PDF parser to extract data from PDFs."""
    logger.info("=" * 80)
    logger.info("STEP 1: Starting PDF parser...")
    logger.info("=" * 80)

    from db_utils import get_session
    from pdf_parser import process_pdf

    session = get_session()
    try:
        pdf_dir = Path("pdfs")
        cache_dir = Path("ocr_cache")
        cache_dir.mkdir(exist_ok=True)

        pdf_files = sorted(pdf_dir.glob("*.pdf"))
        total = len(pdf_files)
        logger.info(f"Found {total} PDF files to process")

        for i, pdf_path in enumerate(pdf_files, 1):
            logger.info(f"Processing [{i}/{total}]: {pdf_path.name}")
            try:
                process_pdf(session, pdf_path, cache_dir=cache_dir)
            except Exception as e:
                logger.error(f"Failed to process {pdf_path.name}: {e}")
                continue

        session.commit()
        logger.info("PDF parser completed successfully")
        return True
    except Exception as e:
        logger.error(f"PDF parser failed: {e}")
        session.rollback()
        return False
    finally:
        session.close()


def run_web_scraper():
    """Run the web scraper to enrich data from drillingedge.com."""
    logger.info("=" * 80)
    logger.info("STEP 2: Starting web scraper to enrich data from drillingedge.com...")
    logger.info("=" * 80)

    from db_utils import get_session, Well
    from web_scraper import scrape_well_data

    session = get_session()
    try:
        # Get all wells
        wells = session.query(Well).all()
        total = len(wells)
        logger.info(f"Found {total} wells to enrich")

        for i, well in enumerate(wells, 1):
            logger.info(f"Processing [{i}/{total}]: {well.api} / {well.well_name}")

            # Always force scrape to get coordinates and correct data
            try:
                scraped_data = scrape_well_data(well.api, well.well_name, well.county_state)

                # Update all fields with web-scraped data
                if scraped_data.get('well_name'):
                    well.well_name = scraped_data['well_name']
                if scraped_data.get('operator'):
                    well.operator = scraped_data['operator']
                if scraped_data.get('county_state'):
                    well.county_state = scraped_data['county_state']
                if scraped_data.get('well_status'):
                    well.well_status = scraped_data['well_status']
                if scraped_data.get('well_type'):
                    well.well_type = scraped_data['well_type']
                if scraped_data.get('closest_city'):
                    well.closest_city = scraped_data['closest_city']
                if scraped_data.get('barrels_oil_produced') is not None:
                    well.barrels_oil_produced = scraped_data['barrels_oil_produced']
                if scraped_data.get('gas_produced') is not None:
                    well.gas_produced = scraped_data['gas_produced']
                if scraped_data.get('latitude') is not None:
                    well.latitude = scraped_data['latitude']
                if scraped_data.get('longitude') is not None:
                    well.longitude = scraped_data['longitude']

                session.commit()
                logger.info(f"  ✓ Enriched {well.api}: {well.well_name} at ({well.latitude}, {well.longitude})")
            except Exception as e:
                logger.error(f"  ✗ Failed to enrich {well.api}: {e}")
                session.rollback()
                continue

        logger.info("Web scraper completed successfully")
        return True
    except Exception as e:
        logger.error(f"Web scraper failed: {e}")
        session.rollback()
        return False
    finally:
        session.close()


def cleanup_invalid_wells():
    """Remove wells with invalid API numbers (not ND wells)."""
    logger.info("=" * 80)
    logger.info("STEP 3: Cleaning up invalid wells...")
    logger.info("=" * 80)

    from db_utils import get_session, Well, StimulationData

    session = get_session()
    try:
        # Find invalid wells
        invalid_wells = session.query(Well).filter(~Well.api.like('33-%')).all()

        if invalid_wells:
            logger.info(f"Found {len(invalid_wells)} wells with invalid API numbers")
            for well in invalid_wells:
                logger.info(f"  Deleting: {well.api} / {well.well_name}")
                # Delete stimulation data first
                session.query(StimulationData).filter(StimulationData.well_id == well.id).delete()
                session.delete(well)

            session.commit()
            logger.info(f"Deleted {len(invalid_wells)} invalid wells")
        else:
            logger.info("No invalid wells found")

        return True
    except Exception as e:
        logger.error(f"Cleanup failed: {e}")
        session.rollback()
        return False
    finally:
        session.close()


def main():
    logger.info("=" * 80)
    logger.info("STARTING COMBINED DATA PROCESSING PIPELINE")
    logger.info("=" * 80)

    # Step 1: Parse PDFs
    if not run_pdf_parser():
        logger.error("PDF parsing failed, stopping pipeline")
        sys.exit(1)

    # Step 2: Enrich with web data
    if not run_web_scraper():
        logger.error("Web scraping failed, stopping pipeline")
        sys.exit(1)

    # Step 3: Cleanup invalid wells
    if not cleanup_invalid_wells():
        logger.error("Cleanup failed, but continuing...")

    logger.info("=" * 80)
    logger.info("DATA PROCESSING PIPELINE COMPLETED SUCCESSFULLY!")
    logger.info("Database is fully populated and ready")
    logger.info("=" * 80)


if __name__ == "__main__":
    main()
