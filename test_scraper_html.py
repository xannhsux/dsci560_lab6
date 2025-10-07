"""Test the web scraper with actual HTML from drillingedge.com"""

from bs4 import BeautifulSoup
import sys
sys.path.insert(0, 'src')
from web_scraper import _clean_text, _parse_number

# Actual HTML structure from drillingedge.com
meta_info_html = """
<section itemscope="" itemtype="https://schema.org/LocalBusiness" class="meta_info">
<div>Well Name: <span class="detail_point">Alley Cat 17 20 Federal Com 715H</span></div>
<div>API #: <span class="detail_point">30-025-52907</span></div>
<div>Operator: <span class="detail_point"><a href="#">Devon Energy Production Company, LP</a></span></div>
<div>County: <span class="detail_point">Lea County, NM</span></div>
<div>Production Dates on File: <span class="detail_point">October 2024 to July 2025</span></div>
<p class="block_stat"><span class="dropcap">23.4 k</span> Barrels of Oil Produced in Jul 2025</p>
<p class="block_stat"><span class="dropcap">68.5 k</span> MCF of Gas Produced in Jul 2025</p>
</section>
"""

well_table_html = """
<article class="well_table">
<table class="skinny">
<tbody><tr>
<th>Well Name</th><td colspan="3">Alley Cat 17 20 Federal Com 715H</td>
<th>API No.</th><td>30-025-52907</td>
<th>Well Direction</th><td></td>
</tr>
<tr>
<th>Operator</th><td colspan="3">DEVON ENERGY PRODUCTION COMPANY, LP</td>
<th>Lease No.</th><td>322236</td>
<th>Field / Formation</th><td></td>
</tr>
<tr>
<th>Well Status</th><td>New</td>
<th>Well Type</th><td>Oil</td>
<th>Township Range Section</th><td colspan="3">23S 32E 17</td>
</tr>
<tr>
<th>County</th><td>Lea County, NM</td>
<th>Closest City</th><td>Malaga</td>
<th>Latitude / Longitude</th><td colspan="3">32.311263, -103.696115</td>
</tr>
</tbody></table>
</article>
"""

print("Testing HTML Parsing with Actual drillingedge.com Structure")
print("=" * 70)

# Test meta_info extraction
print("\n1. Testing meta_info section (production data):")
print("-" * 70)
soup = BeautifulSoup(meta_info_html, 'html.parser')
meta_info = soup.find('section', class_='meta_info')

if meta_info:
    block_stats = meta_info.find_all('p', class_='block_stat')
    print(f"Found {len(block_stats)} block_stat elements")

    oil_produced = None
    gas_produced = None

    for stat in block_stats:
        stat_text = stat.get_text()

        if 'Barrels of Oil' in stat_text:
            dropcap = stat.find('span', class_='dropcap')
            if dropcap:
                value_text = dropcap.get_text().strip()
                oil_produced = _parse_number(value_text)
                print(f"  Oil: '{value_text}' -> {oil_produced} barrels")

        elif 'MCF of Gas' in stat_text:
            dropcap = stat.find('span', class_='dropcap')
            if dropcap:
                value_text = dropcap.get_text().strip()
                gas_produced = _parse_number(value_text)
                print(f"  Gas: '{value_text}' -> {gas_produced} MCF")

    print(f"\nExtracted Values:")
    print(f"  barrels_oil_produced: {oil_produced} ✓" if oil_produced == 23400.0 else f"  barrels_oil_produced: {oil_produced} ✗")
    print(f"  gas_produced: {gas_produced} ✓" if gas_produced == 68500.0 else f"  gas_produced: {gas_produced} ✗")

# Test well_table extraction
print("\n2. Testing well_table section (well details):")
print("-" * 70)
soup = BeautifulSoup(well_table_html, 'html.parser')
well_table = soup.find('article', class_='well_table')

well_status = None
well_type = None
closest_city = None

if well_table:
    rows = well_table.find_all('tr')
    print(f"Found {len(rows)} table rows")

    for row in rows:
        headers = row.find_all('th')

        for header in headers:
            header_text = _clean_text(header.get_text())
            if not header_text:
                continue

            value_cell = header.find_next_sibling('td')
            if not value_cell:
                continue

            value_text = _clean_text(value_cell.get_text())

            # Skip "Members Only" values
            if value_text and 'Members Only' in value_text:
                continue

            # Extract fields
            if 'Well Status' in header_text:
                well_status = value_text
                print(f"  Well Status: '{value_text}'")
            elif 'Well Type' in header_text:
                well_type = value_text
                print(f"  Well Type: '{value_text}'")
            elif 'Closest City' in header_text:
                closest_city = value_text
                print(f"  Closest City: '{value_text}'")

    print(f"\nExtracted Values:")
    print(f"  well_status: '{well_status}' ✓" if well_status == "New" else f"  well_status: '{well_status}' ✗")
    print(f"  well_type: '{well_type}' ✓" if well_type == "Oil" else f"  well_type: '{well_type}' ✗")
    print(f"  closest_city: '{closest_city}' ✓" if closest_city == "Malaga" else f"  closest_city: '{closest_city}' ✗")

# Summary
print("\n" + "=" * 70)
print("SUMMARY:")
print("=" * 70)
all_correct = (
    oil_produced == 23400.0 and
    gas_produced == 68500.0 and
    well_status == "New" and
    well_type == "Oil" and
    closest_city == "Malaga"
)

if all_correct:
    print("✅ All fields extracted correctly!")
    print("\nThe scraper should work properly with real drillingedge.com pages.")
else:
    print("❌ Some fields were not extracted correctly.")
    print("   Review the parsing logic.")
