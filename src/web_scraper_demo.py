"""Demo/Mock web scraper for testing when drillingedge.com is unavailable.

This module provides simulated data for testing the web scraping integration
without actually accessing external websites. Useful for development and testing.
"""

from __future__ import annotations

import hashlib
import random
from typing import Dict, Optional


# Demo data templates based on typical well information
DEMO_STATUSES = ["Active", "Inactive", "Plugged and Abandoned", "Drilling", "Producing"]
DEMO_TYPES = ["Oil & Gas", "Oil", "Gas", "Injection", "Water Disposal"]
DEMO_CITIES = ["Watford City", "Williston", "Tioga", "Stanley", "Keene", "Ray", "Alexander"]


def _generate_demo_data(api: str) -> Dict[str, Optional[str]]:
    """Generate consistent demo data based on API number hash."""
    # Use API as seed for consistent results per well
    seed = int(hashlib.md5(api.encode()).hexdigest()[:8], 16)
    random.seed(seed)

    # Generate realistic production numbers
    oil_produced = round(random.uniform(100, 5000), 1)
    gas_produced = round(random.uniform(0.5, 50), 1)

    return {
        'well_status': random.choice(DEMO_STATUSES),
        'well_type': random.choice(DEMO_TYPES),
        'closest_city': random.choice(DEMO_CITIES),
        'barrels_oil_produced': oil_produced,
        'gas_produced': gas_produced,
    }


def scrape_well_data_demo(api: str, well_name: str) -> Dict[str, Optional[str]]:
    """Mock scraping function that returns demo data.

    This function mimics the interface of web_scraper.scrape_well_data()
    but returns simulated data instead of scraping real websites.

    Args:
        api: The API number of the well
        well_name: The name of the well

    Returns:
        Dictionary containing simulated scraped fields
    """
    if not api and not well_name:
        # Return defaults if no identifier
        return {
            'well_status': 'N/A',
            'well_type': 'N/A',
            'closest_city': 'N/A',
            'barrels_oil_produced': 0.0,
            'gas_produced': 0.0,
        }

    # Generate demo data
    return _generate_demo_data(api or well_name)


if __name__ == "__main__":
    # Test with sample wells
    test_cases = [
        ("33-053-06057", "Kline Federal 5300 31-18 6B"),
        ("33-053-12345", "Test Well A"),
        ("33-053-67890", "Test Well B"),
    ]

    print("DEMO WEB SCRAPER - Sample Data\n")
    print("=" * 60)

    for api, name in test_cases:
        print(f"\nWell: {name}")
        print(f"API: {api}")
        print("-" * 60)

        data = scrape_well_data_demo(api, name)
        for key, value in data.items():
            print(f"  {key:25s}: {value}")
