# DSCI 560 Lab 6 - Demo Video Script
## Oil Well Data Pipeline and Analysis System

---

## Opening (30 seconds)
**[Show title slide]**

**Speaker:**
"Hello! This is [Team Name/Number] presenting our DSCI 560 Lab 6 project on Oil Well Data Pipeline and Analysis.

Team members:
- [Member 1 Name]
- [Member 2 Name]
- [Member 3 Name]
- [Add all team members]

Today we'll demonstrate our complete data pipeline that extracts information from PDF well reports, enriches it with web-scraped production data, and stores everything in a MySQL database for analysis."

---

## Part 1: System Architecture Overview (1 minute)
**[Show docker-compose.yml or architecture diagram]**

**Speaker:**
"Our solution uses a Docker-based microservices architecture with four main components:

1. **MySQL Database** - Stores well information and stimulation data
2. **PDF Parser Service** - Extracts data from 60 well completion reports using OCR
3. **Web Scraper Service** - Enriches well data by scraping drillingedge.com for production metrics
4. **Flask Web Application** - Provides a user interface to query and visualize the data

All services are orchestrated using Docker Compose, making the solution portable and easy to deploy."

**[Show terminal with docker-compose ps command]**

```bash
cd dsci560_lab6
docker compose ps
```

---

## Part 2: Database Schema (1.5 minutes)
**[Show database schema diagram or MySQL Workbench]**

**Speaker:**
"Let me show you our database schema. We have two main tables:

**Wells Table:**
- Stores well metadata: API number, well name, operator, county/state
- Geographic coordinates: latitude, longitude, datum
- Web-scraped production data: well status, well type, closest city, oil/gas production
- Timestamps for data tracking

**Stimulation Table:**
- Stores fracturing/completion data extracted from treatment summaries
- Formation data: stimulated_formation, top_ft (depth), bottom_ft (depth)
- Treatment details: type_treatment, acid percentage, volume, volume_units
- Proppant data: lbs_proppant (total pounds used)
- Pressure/rate metrics: max_treatment_pressure (PSI), max_treatment_rate
- Number of stimulation_stages
- Date stimulated and detailed treatment notes
- Links to wells table via API number foreign key

Let's connect to the database and inspect the schema:"

**[Show terminal]**

```bash
docker compose exec db mysql -u oil_user -poil_pass oil_wells

DESCRIBE wells;
DESCRIBE stimulation;
```

**Speaker:**
"Notice the foreign key relationship between stimulation.api and wells.api - this ensures referential integrity and allows us to join production data with completion data."

---

## Part 3: Data Preprocessing & ETL Pipeline (3 minutes)

### PDF Extraction Process
**[Show pdf_parser.py code or flowchart]**

**Speaker:**
"Now let's talk about our data preprocessing pipeline. This was the most challenging part of the project.

**Step 1: PDF Text Extraction**

We implemented a hybrid OCR approach:
- First, we try PyPDF2 for fast text extraction
- For pages with poor quality (tables that lose structure), we automatically detect issues using validation functions
- Failed pages are re-processed with Tesseract OCR, which preserves table structure better

**[Show validation code snippet]**

The validation functions check:
- API numbers follow North Dakota format (33-XXX-XXXXX)
- Operator names contain company markers (LLC, Inc, Corporation)
- County/State fields aren't confused with dates

**Step 2: Pattern Matching with Regex**

We use comprehensive regex patterns to extract well metadata:
- API numbers in various formats
- Well names (cleaning artifacts like 'Job Number' prefixes)
- Operator names (with OCR error correction)
- Coordinates in both DMS (degrees/minutes/seconds) and decimal formats
- Geographic data: county, state, SHL (surface hole location), datum

**[Show WELL_PATTERNS dictionary]**

And stimulation/completion data:
- Formation name and depth interval (top_ft, bottom_ft)
- Number of stimulation stages
- Treatment type and acid percentage
- Proppant amount (lbs_proppant)
- Treatment volume and units (BBL, gallons, etc.)
- Maximum treatment pressure (PSI) and rate (BPM)
- Date stimulated
- Detailed treatment notes

**[Show STIM_PATTERNS dictionary or example extraction]**

**Step 3: OCR Error Correction**

Common OCR errors we handle:
- Leading digits before API numbers: '2633-053-06025' → '33-053-06025'
- Table structure loss causing field misalignment
- Date/county confusion: '9-Nov-14' vs 'McKenzie Co., ND'
- Operator gibberish: 'Otr-Otr swsw' → re-OCR with Tesseract

**[Show example PDF and extracted data]**

Let's run the parser on a sample PDF:"

**[Show terminal]**

```bash
docker compose run --rm pdf_parser python -m src.pdf_parser ./pdfs/W28651.pdf --cache-dir ./ocr_cache
```

**Speaker:**
"Notice how it detected validation failures and retried with Tesseract, successfully extracting:
- API: 33-053-06025
- Operator: Oasis Petroleum North America LLC
- County: McKenzie Co., ND
- Coordinates: Lat 48.018056, Lon -103.605000"

### Web Scraping Process
**[Show web_scraper.py code]**

**Speaker:**
"**Step 4: Web Scraping for Production Data**

After extracting basic metadata from PDFs, we enrich each well record by scraping drillingedge.com.

**URL Construction:**
We build direct URLs using the pattern:
`https://www.drillingedge.com/{state}/{county}/wells/{well-name-slug}/{api}`

For example:
- API: 33-053-06025
- Well name: Kline Federal 5300 41-18 9T
- URL: https://www.drillingedge.com/north-dakota/mckenzie-county/wells/kline-federal-5300-41-18-9t/33-053-06025

**Data Extracted:**
- Well status (Active, Inactive, etc.)
- Well type (Oil, Gas, SWD)
- Closest city
- Cumulative oil production (barrels)
- Cumulative gas production (MCF)

Let's run the web scraper:"

**[Show terminal]**

```bash
docker compose run --rm web_scraper python -m src.enrich_wells --limit 5 --verbose
```

**Speaker:**
"The scraper successfully enriched 46 out of 60 wells. The remaining 14 wells either had incorrect APIs (now fixed with our Tesseract retry) or are SWD (salt water disposal) wells not listed on the website."

---

## Part 4: Query Demonstrations (2.5 minutes)
**[Show terminal or web interface]**

**Speaker:**
"Now let's demonstrate some analytical queries on our populated database.

**Query 1: Top Oil Producers by Operator**

Let's find which operators have the highest cumulative oil production:"

```sql
SELECT
    operator,
    COUNT(*) as well_count,
    SUM(barrels_oil_produced) as total_oil_barrels,
    AVG(barrels_oil_produced) as avg_oil_per_well
FROM wells
WHERE barrels_oil_produced > 0
GROUP BY operator
ORDER BY total_oil_barrels DESC;
```

**Speaker:**
"We can see Oasis Petroleum and Continental Resources are the top producers in our dataset.

**Query 2: Wells by County and Status**

Let's analyze well distribution and activity by county:"

```sql
SELECT
    county_state,
    well_status,
    COUNT(*) as count,
    AVG(barrels_oil_produced) as avg_production
FROM wells
GROUP BY county_state, well_status
ORDER BY county_state, count DESC;
```

**Speaker:**
"Most wells are in McKenzie and Williams counties, with the majority being Active producers.

**Query 3: Join Wells with Stimulation Data**

Let's correlate stimulation techniques with production:"

```sql
SELECT
    w.well_name,
    w.operator,
    w.barrels_oil_produced,
    s.stimulated_formation,
    s.stimulation_stages,
    s.lbs_proppant,
    s.max_treatment_pressure,
    s.max_treatment_rate,
    s.type_treatment,
    (s.bottom_ft - s.top_ft) as treatment_interval_ft
FROM wells w
INNER JOIN stimulation s ON w.api = s.api
WHERE w.barrels_oil_produced > 100000
ORDER BY w.barrels_oil_produced DESC
LIMIT 10;
```

**Speaker:**
"This shows high-producing wells and their completion parameters. We can analyze:
- Which formations are most productive
- Whether higher proppant volumes (lbs_proppant) correlate with production
- If treatment pressure or rate affects well performance
- The depth interval treated (bottom_ft - top_ft)

**Query 4: Stimulation Data Analysis**

Let's analyze treatment statistics by formation:"

```sql
SELECT
    stimulated_formation,
    COUNT(*) as well_count,
    AVG(stimulation_stages) as avg_stages,
    AVG(lbs_proppant) as avg_proppant_lbs,
    AVG(max_treatment_pressure) as avg_max_pressure,
    AVG(max_treatment_rate) as avg_max_rate,
    AVG(bottom_ft - top_ft) as avg_interval_ft
FROM stimulation
WHERE stimulated_formation IS NOT NULL
GROUP BY stimulated_formation
ORDER BY well_count DESC;
```

**Speaker:**
"This shows treatment statistics by formation. For example:
- Bakken/Three Forks formations typically use 30-40 stages
- Average proppant loading is 10-15 million pounds per well
- Treatment pressures range from 8,000-12,000 PSI
- Typical treatment intervals span 8,000-10,000 feet

**Query 5: Geographic Analysis**

Let's find wells within a specific geographic area:"

```sql
SELECT
    well_name,
    operator,
    latitude,
    longitude,
    barrels_oil_produced,
    SQRT(POW(latitude - 48.0, 2) + POW(longitude - (-103.6), 2)) as distance
FROM wells
WHERE latitude IS NOT NULL
  AND longitude IS NOT NULL
ORDER BY distance
LIMIT 10;
```

**Speaker:**
"This finds wells near a specific coordinate, useful for analyzing spatial clustering of production."

---

## Part 5: Web Application Demo (1.5 minutes)
**[Show web browser with Flask app]**

**Speaker:**
"Finally, let's look at our web interface. Navigate to localhost:8080:"

**[Show homepage]**

**Speaker:**
"The homepage displays:
- Total well count
- Active vs inactive wells
- Top operators
- Average production metrics

**[Click on 'View All Wells']**

We can browse all wells with their complete metadata and production data.

**[Click on a specific well]**

Each well detail page shows:
- Complete metadata (API, operator, location)
- Production statistics
- Stimulation/completion data
- Map with well location (if coordinates available)

**[Demonstrate search/filter]**

Users can filter by:
- Operator
- County
- Well status
- Production range"

---

## Part 6: Design Decisions & Reasoning (2 minutes)

**Speaker:**
"Let me explain our key design decisions:

**1. Hybrid OCR Approach (PyPDF2 + Tesseract)**

*Decision:* Use PyPDF2 first, fall back to Tesseract only when validation fails

*Reasoning:*
- PyPDF2 is 10x faster than Tesseract
- Most pages extract correctly with PyPDF2
- Tesseract preserves table structure better
- Validation-based retry gives us speed AND accuracy

**2. Validation Functions Before Database Insert**

*Decision:* Check data quality before committing to database

*Reasoning:*
- Prevents garbage data from corrupting the database
- Automatic error detection and correction
- Reduces manual data cleaning

**3. Page-Level OCR Caching**

*Decision:* Cache extracted text by page with MD5 hash verification

*Reasoning:*
- Avoid re-processing 60 PDFs during development
- Hash verification ensures cache validity
- Saves hours of processing time

**4. Microservices Architecture**

*Decision:* Separate services for parsing, scraping, and web app

*Reasoning:*
- Services can scale independently
- Easier debugging and development
- Can run parser/scraper as scheduled jobs
- Web app remains responsive

**5. Foreign Key Constraints**

*Decision:* Enforce referential integrity between wells and stimulation

*Reasoning:*
- Prevents orphaned stimulation records
- Ensures data consistency
- Simplifies join queries

**6. Direct URL Construction for Web Scraping**

*Decision:* Build URLs directly instead of using search function

*Reasoning:*
- 10x faster than navigating search results
- More reliable and predictable
- Avoids pagination issues

**7. Regex Pattern Priority Ordering**

*Decision:* More specific patterns before generic ones

*Reasoning:*
- Prevents false matches
- Handles OCR variations gracefully
- Example: 'Latitude of Well Head' before generic 'Latitude'"

---

## Part 7: Data Quality & Results (1 minute)

**Speaker:**
"Let's review our final data quality:

**[Show database statistics query]**

```sql
SELECT
    COUNT(*) as total_wells,
    COUNT(DISTINCT operator) as unique_operators,
    SUM(CASE WHEN well_status = 'Active' THEN 1 ELSE 0 END) as active_wells,
    SUM(CASE WHEN barrels_oil_produced > 0 THEN 1 ELSE 0 END) as wells_with_production,
    AVG(barrels_oil_produced) as avg_oil_production,
    SUM(CASE WHEN latitude IS NOT NULL THEN 1 ELSE 0 END) as wells_with_coords
FROM wells;
```

**Results:**
- 60 wells processed from PDFs
- 46 wells successfully enriched with production data
- ~55+ wells with valid coordinates
- All wells have correct ND API numbers (33-XXX-XXXXX format)
- All operators properly identified

**Before our Tesseract retry strategy:**
- 7 wells had completely wrong API numbers
- Several wells had garbled operator names
- Some coordinates were missing or incorrect

**After implementing validation + Tesseract retry:**
- 100% correct API extraction
- 100% valid operator names
- Significantly improved coordinate accuracy"

---

## Closing (30 seconds)

**Speaker:**
"In summary, we've built a complete data pipeline that:
- Extracts data from 60 unstructured PDF reports using intelligent OCR
- Enriches well data with production metrics from web sources
- Stores everything in a normalized MySQL database
- Provides a web interface for analysis and visualization

Our hybrid OCR approach with validation-based retry ensures data quality while maintaining performance.

Thank you for watching! Questions?"

**[Show team slide with names again]**

---

## Technical Notes for Recording

### Terminal Commands to Prepare
```bash
# Start services
cd dsci560_lab6
docker compose up -d

# Check services
docker compose ps

# Database queries
docker compose exec db mysql -u oil_user -poil_pass oil_wells

# Show sample PDF extraction
docker compose run --rm pdf_parser python -c "
import sys
from pathlib import Path
sys.path.insert(0, 'src')
from pdf_parser import extract_text_from_pdf, parse_well_info
doc = extract_text_from_pdf(Path('pdfs/W28651.pdf'), cache_dir=Path('ocr_cache'))
data = parse_well_info(doc)
print(f'API: {data.get(\"api\")}')
print(f'Operator: {data.get(\"operator\")}')
print(f'County: {data.get(\"county_state\")}')
"

# Show web scraper
docker compose run --rm web_scraper python -m src.enrich_wells --limit 3 --verbose

# Web browser
open http://localhost:8080
```

### Visual Aids to Prepare
1. Architecture diagram showing all services
2. Database schema diagram (ERD)
3. Flowchart of PDF extraction with Tesseract retry
4. Example PDF page showing "Well Information" table
5. Screenshot of drillingedge.com well page
6. Before/after examples of fixed OCR errors

### Video Recording Tips
- Keep total length under 10 minutes
- Screen share with good resolution (1920x1080)
- Use multiple browser tabs/windows pre-loaded
- Have terminal windows with commands ready
- Test all demos before recording
- Speak clearly and at moderate pace
- Pause briefly between sections
