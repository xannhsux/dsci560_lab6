#!/usr/bin/env python3
"""
OCR Demo Script - Shows how PDF parsing and OCR work step-by-step.

This script demonstrates the hybrid OCR approach:
1. Fast extraction with PyPDF2 (text-based PDFs)
2. Tesseract OCR for scanned/image-based PDFs
3. Validation-based retry (if data quality is poor, retry with Tesseract)

Run this script to see live OCR processing on a sample PDF.
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

# Import our PDF parsing modules
sys.path.insert(0, 'src')
from pdf_parser import extract_text_from_pdf, parse_well_info, identify_pages_for_retry, retry_pages_with_tesseract


def demo_ocr_on_pdf(pdf_name: str):
    """Demonstrate OCR processing on a single PDF."""

    pdf_path = Path(f"pdfs/{pdf_name}")
    cache_dir = Path("ocr_cache")
    cache_dir.mkdir(exist_ok=True)

    if not pdf_path.exists():
        logger.error(f"PDF not found: {pdf_path}")
        logger.info(f"Available PDFs in pdfs/ directory:")
        for p in sorted(Path("pdfs").glob("*.pdf"))[:10]:
            logger.info(f"  - {p.name}")
        return

    logger.info("=" * 80)
    logger.info(f"DEMO: OCR Processing for {pdf_name}")
    logger.info("=" * 80)

    # STEP 1: Initial extraction with PyPDF2
    logger.info("\nSTEP 1: Attempting fast extraction with PyPDF2...")
    doc = extract_text_from_pdf(pdf_path, cache_dir=cache_dir)

    logger.info(f"Extracted {len(doc['pages'])} pages")
    logger.info(f"Extraction methods used: {doc.get('methods', {})}")

    # STEP 2: Parse well information
    logger.info("\nSTEP 2: Parsing well information from extracted text...")
    well_data = parse_well_info(doc)

    logger.info("\nExtracted Well Data:")
    logger.info(f"  API Number:       {well_data.get('api', 'N/A')}")
    logger.info(f"  Well Name:        {well_data.get('well_name', 'N/A')}")
    logger.info(f"  Operator:         {well_data.get('operator', 'N/A')}")
    logger.info(f"  County/State:     {well_data.get('county_state', 'N/A')}")
    logger.info(f"  Coordinates:      {well_data.get('latitude', 'N/A')}, {well_data.get('longitude', 'N/A')}")
    logger.info(f"  Job Type:         {well_data.get('job_type', 'N/A')}")

    # STEP 3: Quality validation
    logger.info("\nSTEP 3: Validating extraction quality...")
    pages_to_retry = identify_pages_for_retry(pdf_path, well_data, doc)

    if pages_to_retry:
        logger.warning(f"Quality check failed! Found {len(pages_to_retry)} pages with poor extraction")
        logger.info(f"   Pages needing retry: {pages_to_retry}")

        # STEP 4: Retry with Tesseract OCR
        logger.info("\nSTEP 4: Re-processing with Tesseract OCR (higher accuracy)...")
        logger.info("   This may take 30-60 seconds...")

        doc = retry_pages_with_tesseract(pdf_path, pages_to_retry, doc)
        well_data = parse_well_info(doc)

        logger.info("\nIMPROVED Well Data (after Tesseract OCR):")
        logger.info(f"  API Number:       {well_data.get('api', 'N/A')}")
        logger.info(f"  Well Name:        {well_data.get('well_name', 'N/A')}")
        logger.info(f"  Operator:         {well_data.get('operator', 'N/A')}")
        logger.info(f"  County/State:     {well_data.get('county_state', 'N/A')}")
        logger.info(f"  Coordinates:      {well_data.get('latitude', 'N/A')}, {well_data.get('longitude', 'N/A')}")
    else:
        logger.info("Quality check passed! PyPDF2 extraction was sufficient")

    # Show a sample of extracted text
    logger.info("\nSample of extracted text (first 500 characters):")
    logger.info("-" * 80)
    sample_text = doc.get('full_text', '')[:500]
    logger.info(sample_text)
    logger.info("-" * 80)

    logger.info("\n" + "=" * 80)
    logger.info("OCR DEMO COMPLETE")
    logger.info("=" * 80)


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Demonstrate OCR processing on oil well PDFs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Demo OCR on a specific PDF
  python demo_ocr.py W28651.pdf

  # Demo OCR on first available PDF
  python demo_ocr.py

This demonstrates:
- PyPDF2 text extraction (fast)
- Quality validation
- Tesseract OCR retry (accurate but slower)
- Well data parsing with regex patterns
        """
    )

    parser.add_argument('pdf', nargs='?', default=None,
                       help='PDF filename to process (e.g., W28651.pdf)')

    args = parser.parse_args()

    # If no PDF specified, use the first one in the directory
    if args.pdf is None:
        pdf_files = sorted(Path("pdfs").glob("*.pdf"))
        if not pdf_files:
            logger.error("No PDFs found in pdfs/ directory")
            sys.exit(1)
        pdf_name = pdf_files[0].name
        logger.info(f"No PDF specified, using: {pdf_name}")
    else:
        pdf_name = args.pdf

    demo_ocr_on_pdf(pdf_name)


if __name__ == "__main__":
    main()
