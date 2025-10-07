"""Web scraper for enriching well data from drillingedge.com.

This module provides functionality to fetch well information from drillingedge.com
using direct URL construction based on API numbers, well names, and county information.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Dict, Optional
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup


logger = logging.getLogger(__name__)

# Rate limiting: delay between requests (in seconds)
REQUEST_DELAY = 2.0

# Timeout for HTTP requests (in seconds)
REQUEST_TIMEOUT = 30

# User agent to identify our scraper
USER_AGENT = "Mozilla/5.0 (compatible; WellDataCollector/1.0; +Educational Purpose)"


def _clean_text(text: Optional[str]) -> Optional[str]:
    """Clean and normalize text extracted from HTML."""
    if not text:
        return None
    cleaned = re.sub(r'\s+', ' ', text.strip())
    return cleaned if cleaned else None


def _parse_number(text: Optional[str]) -> Optional[float]:
    """Parse a number from text, removing commas and other formatting.

    Handles formats like:
    - "23.4 k" -> 23400.0 (thousands)
    - "1,234" -> 1234.0
    - "68.5 k" -> 68500.0
    """
    if not text:
        return None
    try:
        # Check if the number is in thousands (e.g., "23.4 k")
        text_lower = text.lower().strip()
        multiplier = 1.0

        if 'k' in text_lower:
            multiplier = 1000.0
            text_lower = text_lower.replace('k', '').strip()

        # Remove commas and other non-numeric characters except decimal point and minus
        cleaned = re.sub(r'[^\d.-]', '', text_lower)

        if cleaned:
            return float(cleaned) * multiplier
        return None
    except ValueError:
        return None


def _slugify_well_name(name: str) -> str:
    """Convert well name to URL slug format.

    Example: "Atlanta 1-6H" -> "atlanta-1-6h"
    """
    if not name:
        return ""
    # Convert to lowercase and replace spaces with hyphens
    slug = name.lower().strip()
    slug = re.sub(r'[^\w\s-]', '', slug)  # Remove special chars except spaces and hyphens
    slug = re.sub(r'[\s_]+', '-', slug)   # Replace spaces/underscores with hyphens
    slug = re.sub(r'-+', '-', slug)        # Collapse multiple hyphens
    return slug.strip('-')


def _extract_state_county(county_state: Optional[str]) -> tuple[str, str]:
    """Extract state and county from county_state field.

    Examples:
        "Williams County, N. Dakota" -> ("north-dakota", "williams-county")
        "McKenzie County, ND" -> ("north-dakota", "mckenzie-county")
        "Williams" -> ("north-dakota", "williams-county")
        "Atlanta 1-6H NWNW 6 {153 N |101 W_ {Williams" -> ("north-dakota", "williams-county")
    """
    state_slug = "north-dakota"  # Default for this dataset
    county_slug = ""

    if not county_state:
        return state_slug, county_slug

    text = county_state.lower()

    # Known North Dakota counties in oil production
    known_counties = ['williams', 'mckenzie', 'dunn', 'mountrail', 'burke', 'divide']

    # Try to find a known county name in the text
    for county in known_counties:
        if county in text:
            county_slug = f"{county}-county"
            return state_slug, county_slug

    # Fallback: extract county name (look for "X County" pattern)
    county_match = re.search(r'([a-z]+)\s+county', text)
    if county_match:
        county_name = county_match.group(1).strip()
        county_slug = f"{county_name}-county"

    return state_slug, county_slug


def construct_well_url(api: str, well_name: str, county_state: Optional[str] = None) -> Optional[str]:
    """Construct the direct URL to a well detail page on drillingedge.com.

    URL structure: https://www.drillingedge.com/{state}/{county}/wells/{well-name-slug}/{api}
    Example: https://www.drillingedge.com/north-dakota/williams-county/wells/atlanta-1-6h/33-105-02732

    Args:
        api: The API number (e.g., "33-105-02732")
        well_name: The well name (e.g., "Atlanta 1-6H")
        county_state: The county/state string (e.g., "Williams County, ND")

    Returns:
        Constructed URL, or None if required data is missing
    """
    if not api or not well_name:
        return None

    # Get state and county slugs
    state_slug, county_slug = _extract_state_county(county_state)

    if not county_slug:
        # Try to infer from API prefix: 33-105 = Williams, 33-053 = McKenzie
        api_parts = api.split('-')
        if len(api_parts) >= 2:
            county_code = api_parts[1]
            if county_code == '105':
                county_slug = 'williams-county'
            elif county_code == '053':
                county_slug = 'mckenzie-county'
            elif county_code == '025':
                county_slug = 'dunn-county'
            else:
                logger.warning(f"Unknown county code {county_code} in API {api}")
                return None
        else:
            return None

    # Convert well name to slug
    well_slug = _slugify_well_name(well_name)
    if not well_slug:
        return None

    # Construct URL
    url = f"https://www.drillingedge.com/{state_slug}/{county_slug}/wells/{well_slug}/{api}"
    logger.debug(f"Constructed URL: {url}")
    return url


def search_well(api: str, well_name: str, county_state: Optional[str], session: requests.Session) -> Optional[str]:
    """Get the URL for a well detail page on drillingedge.com.

    Constructs the URL directly based on well data.

    Args:
        api: The API number of the well
        well_name: The name of the well
        county_state: County/state information to help construct URL
        session: Requests session (kept for compatibility)

    Returns:
        URL of the well detail page if found, None otherwise
    """
    # Construct the URL directly
    url = construct_well_url(api, well_name, county_state)

    if url:
        logger.debug(f"Using constructed URL: {url}")
        return url

    logger.warning(f"Could not construct URL for API {api} / {well_name}")
    return None


def extract_well_details(detail_url: str, session: requests.Session) -> Dict[str, Optional[str]]:
    """Extract well information from a drillingedge.com detail page.

    Args:
        detail_url: URL of the well detail page
        session: Requests session for connection pooling

    Returns:
        Dictionary containing extracted fields (well_status, well_type, closest_city,
        barrels_oil_produced, gas_produced, latitude, longitude)
    """
    result = {
        'well_name': None,
        'operator': None,
        'county_state': None,
        'well_status': None,
        'well_type': None,
        'closest_city': None,
        'barrels_oil_produced': None,
        'gas_produced': None,
        'latitude': None,
        'longitude': None,
    }

    try:
        logger.debug(f"Fetching well details from {detail_url}")
        time.sleep(REQUEST_DELAY)  # Rate limiting

        response = session.get(
            detail_url,
            headers={'User-Agent': USER_AGENT},
            timeout=REQUEST_TIMEOUT
        )
        response.raise_for_status()

        soup = BeautifulSoup(response.content, 'html.parser')

        # Extract well name from page title
        # Format: "Columbus Federal 1-16H | API #33-053-04852 | Continental Resources, Inc."
        title = soup.find('title')
        if title:
            title_text = title.get_text()
            parts = [p.strip() for p in title_text.split('|')]
            if len(parts) >= 1:
                result['well_name'] = parts[0].strip()
            if len(parts) >= 3:
                result['operator'] = parts[2].strip()

        # Extract production data from meta_info section
        # Structure: <section class="meta_info">
        #   <p class="block_stat"><span class="dropcap">111</span> Barrels of Oil Produced in Jul 2025</p>
        #   <p class="block_stat"><span class="dropcap">315</span> MCF of Gas Produced in Jul 2025</p>
        # </section>
        meta_info = soup.find('section', class_='meta_info')

        if meta_info:
            # Find all block_stat paragraphs
            block_stats = meta_info.find_all('p', class_='block_stat')

            for stat in block_stats:
                stat_text = stat.get_text()

                # Extract oil production
                if 'Barrels of Oil' in stat_text or 'barrels of oil' in stat_text.lower():
                    dropcap = stat.find('span', class_='dropcap')
                    if dropcap:
                        value_text = dropcap.get_text().strip()
                        result['barrels_oil_produced'] = _parse_number(value_text)

                # Extract gas production
                elif 'MCF of Gas' in stat_text or 'mcf of gas' in stat_text.lower():
                    dropcap = stat.find('span', class_='dropcap')
                    if dropcap:
                        value_text = dropcap.get_text().strip()
                        result['gas_produced'] = _parse_number(value_text)

        # Find the well_table article which contains the structured data
        well_table = soup.find('article', class_='well_table')

        if well_table:
            # Extract data from table rows
            rows = well_table.find_all('tr')

            for row in rows:
                # Get all th and td elements in the row
                headers = row.find_all('th')

                # Process each header-cell pair
                for header in headers:
                    header_text = _clean_text(header.get_text())
                    if not header_text:
                        continue

                    # Get the corresponding value (next td after this th)
                    value_cell = header.find_next_sibling('td')
                    if not value_cell:
                        continue

                    value_text = _clean_text(value_cell.get_text())

                    # Skip "Members Only" values
                    if value_text and 'Members Only' in value_text:
                        continue

                    # Map header to our result fields
                    if 'Well Status' in header_text:
                        result['well_status'] = value_text
                    elif 'Well Type' in header_text:
                        result['well_type'] = value_text
                    elif 'Closest City' in header_text:
                        result['closest_city'] = value_text
                    elif 'County' in header_text and 'State' in header_text:
                        # Extract county/state like "McKenzie County, North Dakota"
                        result['county_state'] = value_text
                    elif 'Latitude / Longitude' in header_text or 'Latitude/Longitude' in header_text:
                        # Parse "48.109552, -103.731528" format
                        if value_text:
                            coords = value_text.split(',')
                            if len(coords) == 2:
                                try:
                                    result['latitude'] = float(coords[0].strip())
                                    result['longitude'] = float(coords[1].strip())
                                except ValueError:
                                    pass

        logger.info(f"Extracted well details: name={result['well_name']}, "
                   f"operator={result['operator']}, county={result['county_state']}, "
                   f"status={result['well_status']}, type={result['well_type']}, "
                   f"city={result['closest_city']}, oil={result['barrels_oil_produced']}, "
                   f"gas={result['gas_produced']}, lat={result['latitude']}, lon={result['longitude']}")

    except requests.RequestException as exc:
        logger.error(f"Failed to fetch well details from {detail_url}: {exc}")
    except Exception as exc:
        logger.error(f"Error parsing well details from {detail_url}: {exc}")

    return result


def scrape_well_data(api: str, well_name: str, county_state: Optional[str] = None) -> Dict[str, Optional[str]]:
    """Scrape well data from drillingedge.com.

    This is the main entry point for scraping. It constructs the well URL
    and extracts detailed information.

    Args:
        api: The API number of the well
        well_name: The name of the well
        county_state: County/state information for URL construction

    Returns:
        Dictionary containing extracted fields with N/A defaults for missing data
    """
    # Default values per lab requirements
    result = {
        'well_name': None,
        'operator': None,
        'county_state': None,
        'well_status': 'N/A',
        'well_type': 'N/A',
        'closest_city': 'N/A',
        'barrels_oil_produced': 0.0,
        'gas_produced': 0.0,
        'latitude': None,
        'longitude': None,
    }

    if not api and not well_name:
        logger.warning("Cannot scrape well data without API or well name")
        return result

    # Use a session for connection pooling
    with requests.Session() as session:
        # Get the well URL
        detail_url = search_well(api, well_name, county_state, session)

        if not detail_url:
            logger.warning(f"Could not construct well URL for API {api} / {well_name}")
            return result

        # Extract details from the well page
        extracted = extract_well_details(detail_url, session)

        # Merge with defaults (use extracted value if available, otherwise keep default)
        for key in result:
            if extracted.get(key) is not None:
                result[key] = extracted[key]

    return result


if __name__ == "__main__":
    # Example usage for testing
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    test_api = "33-105-02732"
    test_well_name = "Atlanta 1-6H"
    test_county = "Williams County, ND"

    print(f"Scraping data for API {test_api} / {test_well_name}")
    data = scrape_well_data(test_api, test_well_name, test_county)

    print("\nExtracted data:")
    for key, value in data.items():
        print(f"  {key}: {value}")
