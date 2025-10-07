#!/usr/bin/env python3
"""
Web Scraper Demo Script - Shows how well data enrichment works.

This script demonstrates fetching additional well data from drillingedge.com:
1. Constructs well URL from API number and name
2. Scrapes well page for coordinates, production stats, operator info
3. Shows before/after comparison of well data

Run this script to see live web scraping on a sample well.
"""

import sys
import logging
from pathlib import Path

# Set up logging to show detailed output
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Import our web scraping modules
sys.path.insert(0, 'src')
from web_scraper import construct_well_url, scrape_well_data


def demo_web_scraping(api: str, well_name: str = None, county_state: str = None):
    """Demonstrate web scraping for a single well."""

    logger.info("=" * 80)
    logger.info(f"DEMO: Web Scraping for Well API {api}")
    logger.info("=" * 80)

    # STEP 1: Construct URL
    logger.info("\nSTEP 1: Constructing drillingedge.com URL...")
    url = construct_well_url(api, well_name or "unknown", county_state or "McKenzie County, ND")
    logger.info(f"Target URL: {url}")

    # STEP 2: Scrape data
    logger.info("\nSTEP 2: Fetching well data from drillingedge.com...")
    logger.info("This may take a few seconds...")

    scraped_data = scrape_well_data(api, well_name, county_state)

    # STEP 3: Show results
    logger.info("\nSTEP 3: Displaying scraped data...")
    logger.info("-" * 80)
    logger.info("ENRICHED WELL DATA FROM WEB:")
    logger.info(f"  Well Name:        {scraped_data.get('well_name', 'N/A')}")
    logger.info(f"  Operator:         {scraped_data.get('operator', 'N/A')}")
    logger.info(f"  County/State:     {scraped_data.get('county_state', 'N/A')}")
    logger.info(f"  Well Status:      {scraped_data.get('well_status', 'N/A')}")
    logger.info(f"  Well Type:        {scraped_data.get('well_type', 'N/A')}")
    logger.info(f"  Closest City:     {scraped_data.get('closest_city', 'N/A')}")
    logger.info(f"  Latitude:         {scraped_data.get('latitude', 'N/A')}")
    logger.info(f"  Longitude:        {scraped_data.get('longitude', 'N/A')}")
    logger.info(f"  Oil Produced:     {scraped_data.get('barrels_oil_produced', 'N/A')} barrels")
    logger.info(f"  Gas Produced:     {scraped_data.get('gas_produced', 'N/A')} MCF")
    logger.info("-" * 80)

    # STEP 4: Show what was enriched
    logger.info("\nSTEP 4: Data enrichment summary...")
    enrichments = []
    if scraped_data.get('latitude') and scraped_data.get('longitude'):
        enrichments.append(f"Coordinates: ({scraped_data['latitude']}, {scraped_data['longitude']})")
    if scraped_data.get('well_status'):
        enrichments.append(f"Status: {scraped_data['well_status']}")
    if scraped_data.get('barrels_oil_produced'):
        enrichments.append(f"Oil Production: {scraped_data['barrels_oil_produced']} barrels")
    if scraped_data.get('gas_produced'):
        enrichments.append(f"Gas Production: {scraped_data['gas_produced']} MCF")

    logger.info("Successfully enriched well with:")
    for item in enrichments:
        logger.info(f"  - {item}")

    logger.info("\n" + "=" * 80)
    logger.info("WEB SCRAPING DEMO COMPLETE")
    logger.info("=" * 80)

    logger.info("\nNOTE: This data is now ready to be written to the database,")
    logger.info("      updating the well record with coordinates and production stats.")


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Demonstrate web scraping from drillingedge.com",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Demo web scraping for a specific well
  python demo_webscraper.py 33-053-06025

  # Demo with well name and county
  python demo_webscraper.py 33-053-06025 --name "Kline Federal 5300 41-18 9T" --county "McKenzie County, ND"

This demonstrates:
- URL construction from API number
- HTTP scraping with BeautifulSoup
- Parsing well details from HTML tables
- Coordinate extraction
- Production statistics parsing
- How web data enriches PDF-extracted data
        """
    )

    parser.add_argument('api', type=str,
                       help='API number of well to scrape (e.g., 33-053-06025)')
    parser.add_argument('--name', type=str, default=None,
                       help='Well name (optional, used for URL construction)')
    parser.add_argument('--county', type=str, default=None,
                       help='County and state (optional, e.g., "McKenzie County, ND")')

    args = parser.parse_args()

    demo_web_scraping(args.api, args.name, args.county)


if __name__ == "__main__":
    main()
