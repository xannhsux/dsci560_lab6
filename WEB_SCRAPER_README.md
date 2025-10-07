# Web Scraper Component - Part 4 Documentation

## Overview
The web scraper component enriches well data from the database by scraping additional information from drillingedge.com. This implements Part 4 of the lab assignment.

## What It Does
For each well in the database, the scraper:
1. Searches drillingedge.com using the API number and well name
2. Navigates to the well detail page
3. Extracts the following fields:
   - **Well Status** (e.g., "Active", "Inactive")
   - **Well Type** (e.g., "Oil & Gas", "Oil")
   - **Closest City** (nearest city to the well)
   - **Barrels of Oil Produced** (production data)
   - **Gas Produced** (in MCF)

## New Files Created

### 1. `src/web_scraper.py`
Core scraping module with the following functions:
- `scrape_well_data(api, well_name)` - Main entry point for scraping
- `search_well(api, well_name, session)` - Searches for well on drillingedge.com
- `extract_well_details(detail_url, session)` - Extracts data from well detail page

**Features:**
- Rate limiting (2 second delay between requests)
- Error handling for network failures
- Graceful fallbacks when data is not found
- N/A defaults for missing string fields, 0.0 for missing numeric fields

### 2. `src/enrich_wells.py`
Script to iterate through database and enrich wells with scraped data.

**Usage:**
```bash
# Enrich all wells
python src/enrich_wells.py

# Enrich first 5 wells (for testing)
python src/enrich_wells.py --limit 5

# Enrich a specific well
python src/enrich_wells.py --api 33-053-06057

# Force re-scraping of already enriched wells
python src/enrich_wells.py --force

# Verbose logging
python src/enrich_wells.py -v
```

## Database Changes

### Updated Schema
The `Well` model in `src/db_utils.py` now includes:
```python
well_status = Column(String(255))           # Web-scraped status
well_type = Column(String(255))             # Web-scraped type
closest_city = Column(String(255))          # Web-scraped city
barrels_oil_produced = Column(Float)        # Web-scraped oil production
gas_produced = Column(Float)                # Web-scraped gas production (MCF)
```

### Migration
When you first run the services after these changes, SQLAlchemy will automatically create the new columns if they don't exist (thanks to `Base.metadata.create_all()`).

If you have an existing database and encounter issues, you can manually add the columns:
```sql
ALTER TABLE wells ADD COLUMN well_status VARCHAR(255);
ALTER TABLE wells ADD COLUMN well_type VARCHAR(255);
ALTER TABLE wells ADD COLUMN closest_city VARCHAR(255);
ALTER TABLE wells ADD COLUMN barrels_oil_produced FLOAT;
ALTER TABLE wells ADD COLUMN gas_produced FLOAT;
```

## Running the Web Scraper

### Option 1: Using Docker Compose (Recommended)
```bash
# After parsing PDFs and loading data, run the web scraper
docker compose run --rm web_scraper

# To limit to first 5 wells for testing
docker compose run --rm web_scraper python src/enrich_wells.py --limit 5

# To force re-scraping
docker compose run --rm web_scraper python src/enrich_wells.py --force
```

### Option 2: Standalone Python Script
```bash
# Ensure your database is accessible
export DB_HOST=localhost
export DB_PORT=3307
export DB_USER=oil_user
export DB_PASSWORD=oil_pass
export DB_NAME=oil_wells

# Run the enrichment script
python src/enrich_wells.py
```

## API Changes

### Updated Endpoints
All well endpoints now return the new fields:
- `GET /api/wells` - Returns all wells with scraped data
- `GET /api/wells/<api>` - Returns specific well with scraped data

### Example Response
```json
{
  "id": 1,
  "api": "33-053-06057",
  "well_name": "Kline Federal 5300 31-18 6B",
  "operator": "Oasis Petroleum LLC",
  "well_status": "Active",
  "well_type": "Oil & Gas",
  "closest_city": "Watford City",
  "barrels_oil_produced": 303.0,
  "gas_produced": 2.2,
  "latitude": 48.07625,
  "longitude": -103.60972,
  ...
}
```

## Frontend Changes

### Updated Map Popups
The well popups now display:
- Well Status
- Well Type
- Closest City
- Barrels of Oil Produced
- Gas Produced (MCF)

### Visual Indicator
The crawler info section at the bottom of each popup shows:
- If enriched: "Web data enriched from drillingedge.com: Status=Active, Type=Oil & Gas, City=Watford City"
- If not enriched: "Crawler data: Run web_scraper service to enrich wells with data from drillingedge.com."

## Complete Workflow

### Step-by-Step Process
1. **Start core services**
   ```bash
   docker compose up -d mysql backend nginx
   ```

2. **Parse PDFs** (Part 1-3)
   ```bash
   docker compose run --rm pdf_parser
   ```

3. **Enrich with web data** (Part 4)
   ```bash
   docker compose run --rm web_scraper
   ```

4. **Verify in browser**
   - Open http://localhost:8080
   - Click on any well marker
   - Check that the popup shows scraped fields

5. **Verify via API**
   ```bash
   curl http://localhost:8080/api/wells | jq '.[0]'
   ```

## Rate Limiting and Ethics

The scraper includes responsible scraping practices:
- **2-second delay** between requests to avoid overwhelming the target server
- **30-second timeout** for HTTP requests
- **User-Agent identification** indicating educational purpose
- **Error handling** to gracefully handle failures without retrying excessively

## Troubleshooting

### Issue: No data being scraped
**Possible causes:**
1. drillingedge.com structure has changed (selectors need updating)
2. Network connectivity issues
3. Website blocking automated requests

**Solution:**
- Run with verbose logging: `python src/enrich_wells.py -v`
- Check the logs for specific error messages
- Test with a single well: `python src/enrich_wells.py --api 33-053-06057`

### Issue: Database connection failed
**Solution:**
- Ensure MySQL container is running: `docker compose ps`
- Check database credentials in docker-compose.yml
- Verify network connectivity between containers

### Issue: Wells already enriched, want to re-scrape
**Solution:**
```bash
docker compose run --rm web_scraper python src/enrich_wells.py --force
```

## Testing the Scraper Standalone

You can test the scraper without the full pipeline:
```python
from src.web_scraper import scrape_well_data

# Test with known well
api = "33-053-06057"
well_name = "Kline Federal 5300 31-18 6B"

data = scrape_well_data(api, well_name)
print(data)
```

## Notes on Data Quality

- Not all wells may have complete information on drillingedge.com
- Missing fields will be set to "N/A" (strings) or 0.0 (numbers)
- The scraper uses multiple search strategies (API with/without dashes, well name)
- HTML parsing uses flexible selectors to handle variations in page structure

## Performance Considerations

With the 2-second rate limit:
- 10 wells ≈ 20 seconds
- 50 wells ≈ 100 seconds (1.7 minutes)
- 100 wells ≈ 200 seconds (3.3 minutes)

For large datasets, consider:
- Running in batches: `--limit 50`
- Running overnight for full datasets
- Implementing caching to avoid re-scraping

## Future Enhancements

Potential improvements:
1. Cache scraped data to avoid re-scraping unchanged wells
2. Implement concurrent scraping with controlled rate limiting
3. Add more robust error recovery and retry logic
4. Support for additional data sources beyond drillingedge.com
5. Automated scheduling for periodic re-scraping of production data
