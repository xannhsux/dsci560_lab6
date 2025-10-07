#!/usr/bin/env python
"""Fix data quality issues in existing database records.

This script applies validation and cleanup to all wells in the database:
- Validates and normalizes API numbers
- Fixes OCR errors in operator names
- Cleans well names from form artifacts
"""

import logging
import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from db_utils import get_session, Well
from pdf_parser import validate_api_number, clean_operator, clean_well_name, normalise_api_string

logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s: %(message)s'
)
logger = logging.getLogger(__name__)


def main():
    """Fix all data quality issues in the database."""
    session = get_session()

    try:
        wells = session.query(Well).all()
        logger.info(f"Processing {len(wells)} wells for data quality fixes...")

        fixed_apis = 0
        fixed_operators = 0
        fixed_well_names = 0
        invalid_apis = []
        deleted_wells = []

        for well in wells:
            well_id = well.id
            original_api = well.api

            # Fix API number
            if well.api:
                normalized = normalise_api_string(well.api)
                validated_api = validate_api_number(normalized)

                if validated_api != well.api:
                    if validated_api:
                        logger.info(f"  [{well_id}] Fixed API: {well.api} -> {validated_api}")
                        well.api = validated_api
                        fixed_apis += 1
                    else:
                        logger.error(f"  [{well_id}] INVALID API: {well.api} - DELETING WELL")
                        invalid_apis.append((well_id, well.api, well.well_name))
                        # Delete well and its stimulations (cascade)
                        session.delete(well)
                        deleted_wells.append(well_id)
                        continue

            # Fix Operator
            if well.operator:
                fixed_operator = clean_operator(well.operator)
                if fixed_operator and fixed_operator != well.operator:
                    logger.info(f"  [{well_id}] Fixed Operator: {well.operator} -> {fixed_operator}")
                    well.operator = fixed_operator
                    fixed_operators += 1

            # Fix Well Name
            if well.well_name and well.well_name != 'N/A':
                fixed_name = clean_well_name(well.well_name)
                if fixed_name and fixed_name != well.well_name:
                    logger.info(f"  [{well_id}] Fixed Well Name: {well.well_name} -> {fixed_name}")
                    well.well_name = fixed_name
                    fixed_well_names += 1

        # Commit all changes
        session.commit()

        # Print summary
        logger.info(f"\n{'='*70}")
        logger.info("SUMMARY")
        logger.info(f"{'='*70}")
        logger.info(f"Total wells processed: {len(wells)}")
        logger.info(f"Fixed APIs: {fixed_apis}")
        logger.info(f"Fixed Operators: {fixed_operators}")
        logger.info(f"Fixed Well Names: {fixed_well_names}")
        logger.info(f"Deleted wells (invalid APIs): {len(deleted_wells)}")

        if invalid_apis:
            logger.info(f"\n{'='*70}")
            logger.info("DELETED WELLS (Invalid APIs)")
            logger.info(f"{'='*70}")
            for well_id, api, well_name in invalid_apis:
                logger.info(f"  Well ID {well_id}: {api} ({well_name})")

        # Verify final state
        remaining_wells = session.query(Well).count()
        bad_apis = session.query(Well).filter(~Well.api.like('33-%')).count()
        bad_operators = session.query(Well).filter(Well.operator.like('%!%')).count()
        bad_well_names = session.query(Well).filter(Well.well_name.like('%PRODUCTION RATE%')).count()

        logger.info(f"\n{'='*70}")
        logger.info("FINAL DATABASE STATE")
        logger.info(f"{'='*70}")
        logger.info(f"Total wells: {remaining_wells}")
        logger.info(f"Wells with bad APIs: {bad_apis}")
        logger.info(f"Wells with OCR errors in operator: {bad_operators}")
        logger.info(f"Wells with form headers in well_name: {bad_well_names}")

        if bad_apis == 0 and bad_operators == 0 and bad_well_names == 0:
            logger.info("\n✅ SUCCESS: All data quality issues resolved!")
        else:
            logger.warning("\n⚠️  WARNING: Some issues remain. Review logs above.")

    except Exception as e:
        logger.error(f"Error during database cleanup: {e}")
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()
