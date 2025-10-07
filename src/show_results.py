"""Show web scraping test results"""

import sys
sys.path.insert(0, 'src')
from db_utils import Well, get_session

print('╔' + '═' * 78 + '╗')
print('║' + ' ' * 20 + 'WEB SCRAPING TEST RESULTS' + ' ' * 33 + '║')
print('╚' + '═' * 78 + '╝')
print()

session = get_session()

# Count total wells
total_wells = session.query(Well).count()
enriched_wells = session.query(Well).filter(
    Well.well_status != None,
    Well.well_status != 'Not set'
).count()

print(f'📊 Database Statistics:')
print(f'   Total wells in database: {total_wells}')
print(f'   Enriched wells: {enriched_wells}')
print(f'   Not enriched yet: {total_wells - enriched_wells}')
print()

# Show enriched wells
print('✅ ENRICHED WELLS (WITH WEB-SCRAPED DATA):')
print('─' * 80)

wells = session.query(Well).filter(
    Well.well_status != None,
    Well.well_status != 'Not set'
).all()

for i, well in enumerate(wells, 1):
    print(f'\n{i}. {well.well_name} (API: {well.api})')
    print(f'   ├─ Operator: {well.operator}')
    print(f'   ├─ Location: {well.county_state}')
    print(f'   ├─ 🌐 Well Status: {well.well_status}')
    print(f'   ├─ 🌐 Well Type: {well.well_type}')
    print(f'   ├─ 🌐 Closest City: {well.closest_city}')
    print(f'   ├─ 🌐 Oil Produced: {well.barrels_oil_produced:,.1f} barrels')
    print(f'   └─ 🌐 Gas Produced: {well.gas_produced:,.1f} MCF')

print()
print('─' * 80)
print()

# Show sample of non-enriched wells
print('⏳ SAMPLE OF NON-ENRICHED WELLS (Still showing N/A):')
print('─' * 80)

non_enriched = session.query(Well).filter(
    (Well.well_status == None) | (Well.well_status == 'Not set')
).limit(3).all()

for i, well in enumerate(non_enriched, 1):
    print(f'\n{i}. {well.well_name} (API: {well.api})')
    print(f'   └─ Status: Not enriched yet (would show N/A in web app)')

print()
print('─' * 80)
print()
print('💡 NEXT STEPS:')
print('   1. Open web interface: http://localhost:8080')
print('   2. Click on enriched well markers (like "Atlanta 14-6H")')
print('   3. Verify popup shows clean data with production numbers')
print()
print('   To enrich ALL wells:')
print('   → docker compose run --rm demo_scraper')
print()

session.close()
