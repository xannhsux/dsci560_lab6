"""Utilities for extracting well and stimulation data from PDF files.

The PDFs for the lab are a mixture of digitally generated documents and
scanned images.  We first attempt to pull embedded text via PyPDF2 and fall
back to Tesseract OCR when needed.  The extracted text is then parsed with a
set of relaxed regular expressions so that minor wording differences between
documents do not break the pipeline.

Running the module as a script will iterate over all PDFs inside the provided
folder (defaults to ``./pdfs``) and upsert the parsed data into the configured
database using the SQLAlchemy models defined in ``db_utils``.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple, TypedDict

from PyPDF2 import PdfReader
from pdf2image import convert_from_path
from pdf2image.exceptions import PDFInfoNotInstalledError, PDFPageCountError
import pytesseract
from PIL import ImageFilter, ImageOps

if __package__ in (None, ""):
    import sys

    sys.path.append(str(Path(__file__).resolve().parent))
    from db_utils import Well, StimulationData, get_session
else:  # pragma: no cover - executed when imported as package module
    from .db_utils import Well, StimulationData, get_session


logger = logging.getLogger(__name__)


class DocumentText(TypedDict, total=False):
    full_text: str
    pages: Dict[int, str]
    methods: Dict[int, str]


POPPLER_PATH = os.getenv("POPPLER_PATH")
TESSERACT_CMD = os.getenv("TESSERACT_CMD")
OCR_MANIFEST_PATH = Path(os.getenv("OCR_MANIFEST", "ocr_targets.json"))
if TESSERACT_CMD:
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD


def _load_ocr_manifest() -> Dict[str, Iterable[int]]:
    if not OCR_MANIFEST_PATH.exists():
        return {}
    try:
        with open(OCR_MANIFEST_PATH, "r", encoding="utf-8") as handle:
            data = json.load(handle)
            if isinstance(data, dict):
                return data
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed to load OCR manifest %s: %s", OCR_MANIFEST_PATH, exc)
    return {}


_MANIFEST_CACHE: Optional[Dict[str, Iterable[int]]] = None


def _manifest_entries_for(pdf_path: Path) -> Iterable[int]:
    global _MANIFEST_CACHE
    if _MANIFEST_CACHE is None:
        _MANIFEST_CACHE = _load_ocr_manifest()
    stem = pdf_path.stem
    entries = _MANIFEST_CACHE.get(stem)
    if not entries:
        return []
    return entries


TEXT_KEYWORDS = {
    "well data summary",
    "well information",
    "well name and number",
    "well name & number",
    "well completion or recompletion report",
    "surface hole location",
    "date stimulated",
    "stimulated formation",
    "stimulation stages",
    "lbs proppant",
    "type treatment",
}


def _compute_target_pages(pdf_path: Path, page_texts: Dict[int, str]) -> set[int]:
    target: set[int] = set()

    for index, text in page_texts.items():
        if not text:
            continue
        lowered = text.lower()
        if any(keyword in lowered for keyword in TEXT_KEYWORDS):
            target.add(index)
            for neighbour in (index - 1, index + 1, index + 2):
                if neighbour >= 0:
                    target.add(neighbour)

    for entry in _manifest_entries_for(pdf_path):
        try:
            zero_based = int(entry) - 1
        except (TypeError, ValueError):
            continue
        if zero_based >= 0:
            target.add(zero_based)

    return target


WELL_PATTERNS = {
    "operator": [
        # Match full company names directly (for OCR-damaged tables)
        r"(Oasis Petroleum North America(?:\s+LLC)?)",
        r"(Continental Resources(?:,\s*Inc\.?)?)",
        r"(Whiting (?:Oil and Gas|Petroleum) Corporation)",
        # Standard patterns
        r"Well\s+Operator[:#\s]+([A-Za-z0-9\s,\.&\-]+?(?:LLC|Inc|Corporation|Co|LP)\.?)",
        r"Operator[:#\s]*\n\s*([A-Za-z0-9\s,\.&\-]+?(?:LLC|Inc|Corporation|Co|LP)\.?)",
        r"Operator[:#\s]+([A-Za-z0-9\s,\.&\-]+?(?:LLC|Inc|Corporation|Co|LP)\.?)",
        r"Operator\s+(.*)",
    ],
    "well_name": [
        r"\n([A-Z][A-Za-z0-9\s\-]*?\d+[A-Z]+)\s*\n\s*Well\s+Name",  # Value ABOVE label (reverse order)
        r"Well\s+Name\s+and\s+Number\s*\n\s*([A-Za-z0-9\s\-]+\d+[A-Z]+)",
        r"Well\s+Name\s+and\s+Number\s+([A-Za-z0-9\s\-]+\d+[A-Z]+)",
        r"Well\s+(?:Name\s+)?(?:&\s+)?Number[:#\s]+([A-Za-z0-9\s\-]+\d+[A-Z]+)",
        r"(?:Well\s+or\s+)?Facility\s+Name[:#\s]+([A-Z][A-Za-z0-9\s\-]+\d+[A-Z]+)",
    ],
    "api": [
        # Match ND API number before "Operator" (handles OCR table layout issues where API# label is separated from value)
        r"(\d*33-\d{3}-\d{5})\s+Operator",
        # Match ND API in Well Information context (near Key Offset Wells, Sample Cuts, etc.)
        r"\(TD\)\s*(\d*33-\d{3}-\d{5})",
        # Match standalone ND API number (prioritize ND-specific patterns)
        r"\b(\d{0,2}33-\d{3}-\d{5})\b",
        # Standard patterns
        r"([0-9\-]{10,14})\s*\n\s*API(?:\s*Number|\s*No\.?|\s*#)?",  # Value ABOVE label (reverse order)
        r"API(?:\s*Number|\s*No\.?|\s*#)?[:#\s-]*([0-9\-]{5,})",
        r"API(?:\s*Number|\s*No\.?|\s*#)?[:#\s-]*([0-9\s\-]{5,})",
    ],
    "enseco_job": [
        r"(?:Enseco|Ryan)\s+Job\s*#\s*(\d+)",  # Enseco Job # or Ryan Job #
        r"Enseco\s*Job\s*#[:#\s-]+(\S+)",
    ],
    "job_type": [r"Job\s*Type[:#\s-]+(.+)", r"Type of Job[:#\s-]+(.+)",],
    "county_state": [
        # Direct ND county patterns (for OCR-damaged tables)
        r"(McKenzie Co(?:unty)?\.?,\s*N\.?D\.?)",
        r"(Williams Co(?:unty)?\.?,\s*N\.?D\.?)",
        r"(Dunn Co(?:unty)?\.?,\s*N\.?D\.?)",
        r"(Mountrail Co(?:unty)?\.?,\s*N\.?D\.?)",
        # Standard patterns
        r"County,?\s*State[:#\s-]+(.+)",
        r"County[:#\s-]+(.+)",
    ],
    "shl": [
        r"The\s+SHL\s+is\s+(.+?)(?:The|$)",  # "The SHL is..." format
        r"Surface\s*Hole\s*Location\s*\(SHL\)[:#\s-]+(.+)",
    ],
    "latitude": [
        r"((?:[NS])\s*\d+\s+\d+'\s+[\d.]+\")\s*\n\s*(?:Surface\s+)?Latitude",  # DMS ABOVE label
        r"(?:Surface\s+)?Latitude[:#\s]+((?:[NS])\s*\d+\s+\d+'\s+[\d.]+\")",  # DMS after label (N 48 1' 29")
        r"Latitude[:#\s]+(\d+[°º]\s*\d+\s*['′]\s*[\d.]+\s*\"?\s*(?:North|South|N|S))",  # DMS with flexible spacing
        r"(?:Site\s+Centre\s+)?Latitude[:#\s]+(\d+[°º]\s*\d+\s*['′]\s*[\d.]+\s+[NS])",  # Site Centre format
        r"Latitude\s+of\s+Well\s+Head[:#\s]+(\d+[°º]\s*\d+\s*['′]\s*[\d.]+\s*\"?\s*[NS])",  # "Latitude of Well Head" DMS
        r"Latitude\s+of\s+Well\s+Head[:#\s]+(-?\d+\.\d+)",  # "Latitude of Well Head" decimal
        r"Latitude[:#\s]+([\d.]+)\s+deg",  # Decimal with "deg" suffix
        r"Latitude[:#\s]+(-?\d+\.\d+)",  # Decimal format
        r"Lat(?:itude)?[:#\s]+(-?\d+\.\d+)",
    ],
    "longitude": [
        r"((?:[EW])\s*\d+\s+\d+'\s+[\d.]+\")\s*\n\s*(?:Surface\s+)?Longitude",  # DMS ABOVE label
        r"(?:Surface\s+)?Longitude[:#\s]+((?:[EW])\s*\d+\s+\d+'\s+[\d.]+\")",  # DMS after label (W 103 36' 18")
        r"Longitude[:#\s]+(-?\d+[°º]\s*\d+\s*['′\s]+[\d.,]+\s*\"?\s*(?:East|West|E|W))",  # DMS with flexible spacing
        r"Longitude[:#\s]+(-?\d+[°º]\s*\d+\s*['′]\s*[\d.]+\s+[EW])",  # Short format
        r"Longitude\s+of\s+Well\s+Head[:#\s]+(-?\d+[°º]\s*\d+\s*['′]\s*[\d.]+\s*\"?\s*[EW])",  # "Longitude of Well Head" DMS
        r"Longitude\s+of\s+Well\s+Head[:#\s]+(-?\d+\.\d+)",  # "Longitude of Well Head" decimal
        r"Longitude[:#\s]+(-?[\d.]+)\s+deg",  # Decimal with "deg" suffix
        r"Longitude[:#\s]+(-?\d+\.\d+)",  # Decimal format
        r"Long(?:itude)?[:#\s]+(-?\d+\.\d+)",
    ],
    "datum": [
        r"Longitude[^\n]+Datum[:#\s]+([A-Za-z0-9\s]{3,15})(?:\s|$)",  # Extract from coord line: "... Longitude: ... Datum: Nad 83"
        r"Sea-?Level\s+Datum\s+of\s+([A-Z0-9\s]+)",  # "Sea-Level Datum of NAVD 88"
        r"Datum[:#\s]+((?:NAD|NAVD|WGS)\s*\d{2,4})",  # "Datum: NAD 83" or "Datum: NAVD 88"
        r"Datum[:#\s]+([A-Za-z]{3,10}\s+\d{2,4})",  # "Datum: Nad 83"
    ]
}


STIM_PATTERNS = {
    "date_stimulated": [r"Date\s*Stimulated[:#\s-]+(.+)", r"Stimulated\s*Date[:#\s-]+(.+)"],
    "stimulated_formation": [
        r"Stimulated\s+Formation[:#\s]*\n\s*([A-Za-z0-9\s]{3,30})",
        r"Stimulated\s+Formation[:#\s]+([A-Za-z0-9\s]{3,30}?)(?:\s+\d|\s+\||$)",
        r"\d{1,2}/\d{1,2}/\d{4}\s+(Bakken|Three\s+Forks|3\s+Forks|Middle\s+Bakken|Upper\s+Bakken|MB)",
        r"Formation[:#\s]+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)",
    ],
    "top_ft": [
        r"Top\s*\(ft\)[:#\s]*\n\s*([\d,]+)",
        r"Top\s*\(ft\)[:#\s]+([\d,]+)(?:\s|$)",
        r"Top[:#\s]+([\d,]+)\s*(?:ft|Ft)",
    ],
    "bottom_ft": [
        r"Bottom\s*\(ft\)[:#\s]*\n\s*([\d,]+)",
        r"Bottom\s*\(ft\)[:#\s]+([\d,]+)(?:\s|$)",
        r"Bottom[:#\s]+([\d,]+)\s*(?:ft|Ft)",
    ],
    "stimulation_stages": [
        r"Stimulation\s+Stages[:#\s]*\n\s*(\d+)",
        r"Stimulation\s+Stages[:#\s]+(\d{1,3})(?:\s|$)",
        r"Stages[:#\s]+(\d{1,3})(?:\s+\d{3,})",
    ],
    "volume": [
        r"Volume\s*\(?(?:bbls|gal|m3)?\)?[:#\s-]+([\d,]+(?:\.\d+)?)",
        r"Total\s*Volume[:#\s-]+([\d,]+(?:\.\d+)?)",
    ],
    "volume_units": [
        r"Volume\s*(?:\(([^)]+)\))",
        r"Volume\s*Units[:#\s-]+(\w+)",
    ],
    "type_treatment": [
        r"Type\s+Treatment[:#\s]*\n\s*([A-Za-z\s]{3,20})",
        r"Type\s+Treatment[:#\s]+([A-Za-z]+(?:\s+[A-Za-z]+)?)",
        r"Treatment\s+Type[:#\s]+([A-Za-z]+(?:\s+[A-Za-z]+)?)",
    ],
    "acid": [
        r"Acid[:#\s]+(\d+(?:\.\d+)?%)",
        r"Acid\s+Type[:#\s]+([A-Za-z\s]+)",
        r"Acid[:#\s]+([A-Za-z\s]+(?:Acid)?)",
    ],
    "lbs_proppant": [r"Lbs?\.?\s*Proppant[:#\s-]+([\d,]+)", r"Proppant[:#\s-]+([\d,]+)",],
    "max_treatment_pressure": [r"Max(?:imum)?\s*Treatment\s*Pressure[:#\s-]+([\d,]+)",],
    "max_treatment_rate": [r"Max(?:imum)?\s*Treatment\s*Rate[:#\s-]+([\d,]+(?:\.\d+)?)",],
    "details": [r"Details[:#\s-]+(.+)"]
}


HTML_TAG_RE = re.compile(r"<[^>]+>")
NON_PRINTABLE_RE = re.compile(r"[^\x09\x0A\x0D\x20-\x7E]")
STRING_MISSING_DEFAULT = "N/A"
NUMERIC_MISSING_DEFAULT = 0

# Valid treatment types for validation
VALID_TREATMENTS = {"Slickwater", "Acid", "Hybrid", "Gel", "Foam", "N/A"}

# Known operators from North Dakota for OCR error correction
KNOWN_OPERATORS = {
    "slawson": "Slawson Exploration Company, Inc.",
    "oasis": "Oasis Petroleum North America LLC",
    "continental": "Continental Resources, Inc.",
    "whiting": "Whiting Petroleum Corporation",
    "hess": "Hess Corporation",
    "marathon": "Marathon Oil Company",
    "burlington": "Burlington Resources Oil & Gas Company LP",
}


def check_api_quality(api: str) -> tuple[bool, str]:
    """Check API number extraction quality.

    Returns: (is_valid, reason)
    """
    if not api or api == "N/A":
        return False, "missing"

    # Check for ND API pattern (33-XXX-XXXXX)
    if not re.match(r'^33-\d{3}-\d{5}', api):
        return False, "not_nd_format"

    # Check for OCR errors - leading digits before valid API
    if re.match(r'^\d{2,}33-', api):  # e.g., "2633-053-06025"
        return False, "ocr_prefix_error"

    # Check fallback patterns (14+ digit = likely wrong from fallback extraction)
    if len(re.sub(r'\D', '', api)) > 12:
        return False, "too_long"

    return True, "valid"


def check_operator_quality(operator: str) -> tuple[bool, str]:
    """Check operator name extraction quality.

    Returns: (is_valid, reason)
    """
    if not operator or operator == "N/A":
        return False, "missing"

    # Check for OCR gibberish patterns like "Otr-Otr"
    if re.match(r'^[A-Z][a-z]{1,3}-[A-Z][a-z]{1,3}', operator):
        return False, "ocr_gibberish"

    # Check for single words that are clearly incomplete
    if len(operator.split()) == 1 and len(operator) < 5:
        return False, "too_short"

    # Check for valid company markers
    valid_markers = ['LLC', 'Inc', 'Corporation', 'Co', 'LP', 'Petroleum', 'Resources', 'Oil', 'Energy', 'Exploration']
    if not any(marker in operator for marker in valid_markers):
        return False, "no_company_markers"

    return True, "valid"


def check_county_quality(county_state: str) -> tuple[bool, str]:
    """Check county/state field extraction quality.

    Returns: (is_valid, reason)
    """
    if not county_state or county_state == "N/A":
        return False, "missing"

    # Check for date patterns (common OCR error)
    if re.match(r'\d{1,2}-[A-Z][a-z]{2}-\d{2,4}', county_state):  # "9-Nov-14"
        return False, "date_instead_of_county"

    # Check for ND counties
    nd_counties = ['McKenzie', 'Williams', 'Dunn', 'Mountrail', 'Burke', 'Divide', 'Billings', 'Stark']
    if not any(county in county_state for county in nd_counties):
        return False, "not_nd_county"

    return True, "valid"


def identify_pages_for_retry(pdf_path: Path, well_data: dict,
                             document: DocumentText) -> list[int]:
    """Identify which pages need Tesseract retry based on validation failures.

    Args:
        pdf_path: Path to the PDF file (for logging)
        well_data: Extracted well data dictionary
        document: DocumentText with pages and extraction methods

    Returns:
        List of page numbers that need to be re-OCR'd with Tesseract
    """
    pages_to_retry = set()

    # First priority: Find pages with "Well Information" tables
    # These pages commonly have structure issues with PyPDF2
    for page_num, page_text in document['pages'].items():
        if 'Well Information' in page_text:
            if document.get('methods', {}).get(page_num) == 'pypdf2':
                pages_to_retry.add(page_num)
                logger.debug(f"  Page {page_num} marked for retry (Well Information table)")

    # Check API quality
    api_valid, api_reason = check_api_quality(well_data.get('api', ''))
    if not api_valid:
        logger.info(f"{pdf_path.name}: API validation failed - {api_reason}")
        # Find pages with API-related content that were extracted with PyPDF2
        for page_num, page_text in document['pages'].items():
            if 'API' in page_text.upper() or re.search(r'\d{2,3}-\d{3}-\d{5}', page_text):
                if document.get('methods', {}).get(page_num) == 'pypdf2':
                    pages_to_retry.add(page_num)
                    logger.debug(f"  Page {page_num} marked for retry (API content)")

    # Check Operator quality
    op_valid, op_reason = check_operator_quality(well_data.get('operator', ''))
    if not op_valid:
        logger.info(f"{pdf_path.name}: Operator validation failed - {op_reason}")
        # Find pages with Operator content
        for page_num, page_text in document['pages'].items():
            if 'Operator' in page_text:
                if document.get('methods', {}).get(page_num) == 'pypdf2':
                    pages_to_retry.add(page_num)
                    logger.debug(f"  Page {page_num} marked for retry (Operator content)")

    # Check County quality
    county_valid, county_reason = check_county_quality(well_data.get('county_state', ''))
    if not county_valid:
        logger.info(f"{pdf_path.name}: County validation failed - {county_reason}")
        # Find pages with County/State content
        for page_num, page_text in document['pages'].items():
            if 'County' in page_text or 'State' in page_text:
                if document.get('methods', {}).get(page_num) == 'pypdf2':
                    pages_to_retry.add(page_num)
                    logger.debug(f"  Page {page_num} marked for retry (County content)")

    return sorted(list(pages_to_retry))


def retry_pages_with_tesseract(pdf_path: Path, page_numbers: list[int],
                               document: DocumentText) -> DocumentText:
    """Re-extract specific pages using Tesseract OCR.

    Args:
        pdf_path: Path to the PDF file
        page_numbers: List of page numbers (0-indexed) to re-OCR
        document: Original DocumentText to update

    Returns:
        Updated DocumentText with Tesseract-extracted pages
    """
    if not page_numbers:
        return document

    logger.info(f"Re-OCRing {len(page_numbers)} pages with Tesseract: {page_numbers}")

    # Import PDF to image conversion (same as in extract_text_from_pdf)
    try:
        from pdf2image import convert_from_path
        from pdf2image.exceptions import PDFInfoNotInstalledError, PDFPageCountError
    except ImportError:
        logger.error("pdf2image is required for Tesseract retry. Install with: pip install pdf2image")
        return document

    # Set up Poppler path if needed
    kwargs = {}
    poppler_path = os.getenv("POPPLER_PATH")
    if poppler_path:
        kwargs["poppler_path"] = poppler_path

    # Process each page
    for page_num in page_numbers:
        try:
            # Convert single page to image (first_page and last_page are 1-indexed)
            images = convert_from_path(
                pdf_path,
                first_page=page_num + 1,
                last_page=page_num + 1,
                dpi=300,
                **kwargs,
            )
        except PDFInfoNotInstalledError:
            logger.error(
                f"Poppler is required for Tesseract retry on {pdf_path.name} page {page_num}. "
                "Install it and set POPPLER_PATH if needed."
            )
            continue
        except Exception as exc:
            logger.error(f"Failed to convert {pdf_path.name} page {page_num} to image: {exc}")
            continue

        if not images:
            continue

        # Pre-process image for better OCR
        image = images[0]
        processed = ImageOps.grayscale(image)
        processed = ImageOps.autocontrast(processed, cutoff=2)
        processed = processed.filter(ImageFilter.SHARPEN)

        # Run Tesseract OCR
        try:
            ocr_text = pytesseract.image_to_string(
                processed,
                config="--oem 1 --psm 6 -c preserve_interword_spaces=1",
            )
        except Exception as exc:
            logger.error(f"Tesseract OCR failed for {pdf_path.name} page {page_num}: {exc}")
            continue

        if ocr_text.strip():
            # Update the document with Tesseract-extracted text
            document['pages'][page_num] = ocr_text
            if 'methods' not in document:
                document['methods'] = {}
            document['methods'][page_num] = 'tesseract'
            logger.info(f"  Page {page_num} re-OCR'd successfully with Tesseract")

    # Rebuild full_text from updated pages
    ordered_pages = [document['pages'][idx] for idx in sorted(document['pages'])]
    document['full_text'] = "\n".join(text for text in ordered_pages if text)

    return document


def _compute_file_hash(file_path: Path) -> str:
    """Compute MD5 hash of a file for integrity checking."""
    hash_md5 = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()


def load_from_cache(pdf_path: Path, cache_dir: Path) -> Optional[DocumentText]:
    """Load extracted text from cache if it exists and is valid."""
    cache_file = cache_dir / f"{pdf_path.stem}.json"

    if not cache_file.exists():
        return None

    try:
        with open(cache_file, "r", encoding="utf-8") as f:
            cache_data = json.load(f)

        # Verify PDF hasn't changed by comparing file size and hash
        current_size = pdf_path.stat().st_size
        cached_size = cache_data.get("pdf_size_bytes")

        if current_size != cached_size:
            logger.info("Cache miss for %s: file size changed", pdf_path.name)
            return None

        # Optional: Also verify hash (more thorough but slower)
        current_hash = _compute_file_hash(pdf_path)
        cached_hash = cache_data.get("md5_hash")

        if current_hash != cached_hash:
            logger.info("Cache miss for %s: file hash changed", pdf_path.name)
            return None

        pages: Dict[int, str] = {}
        methods: Dict[int, str] = {}
        for entry in cache_data.get("pages", []):
            try:
                index = int(entry.get("index"))
            except (TypeError, ValueError):
                continue
            pages[index] = entry.get("text", "")
            method = entry.get("method")
            if method:
                methods[index] = method

        document: DocumentText = {
            "full_text": cache_data.get("extracted_text", ""),
            "pages": pages,
        }
        if methods:
            document["methods"] = methods

        logger.info("Cache hit for %s", pdf_path.name)
        return document

    except (json.JSONDecodeError, KeyError, OSError) as exc:
        logger.warning("Failed to load cache for %s: %s", pdf_path.name, exc)
        return None


def save_to_cache(pdf_path: Path, document: DocumentText, cache_dir: Path) -> None:
    """Save extracted text (and per-page breakdown) to cache."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{pdf_path.stem}.json"

    pages_payload = []
    for index, text in (document.get("pages") or {}).items():
        entry = {
            "index": index,
            "text": text,
        }
        method = (document.get("methods") or {}).get(index)
        if method:
            entry["method"] = method
        pages_payload.append(entry)

    cache_data = {
        "source_pdf": pdf_path.name,
        "pdf_path": str(pdf_path.absolute()),
        "extracted_text": document.get("full_text", ""),
        "pages": pages_payload,
        "timestamp": datetime.now().isoformat(),
        "pdf_size_bytes": pdf_path.stat().st_size,
        "md5_hash": _compute_file_hash(pdf_path),
    }

    try:
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(cache_data, f, indent=2, ensure_ascii=False)
        logger.debug("Saved cache for %s", pdf_path.name)
    except OSError as exc:
        logger.warning("Failed to save cache for %s: %s", pdf_path.name, exc)


def _normalise_document_cache(document: Optional[DocumentText]) -> Optional[DocumentText]:
    if document is None:
        return None
    if "pages" not in document and document.get("full_text"):
        document["pages"] = {0: document.get("full_text", "")}
    return document


def extract_text_from_pdf(pdf_path: Path, dpi: int = 300, cache_dir: Optional[Path] = None,
                          use_cache: bool = True, rebuild_cache: bool = False) -> DocumentText:
    """Return textual content from a PDF with per-page breakdown."""

    if cache_dir and use_cache and not rebuild_cache:
        cached = _normalise_document_cache(load_from_cache(pdf_path, cache_dir))
        if cached is not None:
            return cached

    page_texts: Dict[int, str] = {}
    methods: Dict[int, str] = {}
    pages_requiring_ocr = []

    try:
        reader = PdfReader(str(pdf_path))
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.warning("Failed to open text layer for %s: %s", pdf_path, exc)
        reader = None

    if reader is not None:
        for index, page in enumerate(reader.pages):
            try:
                extracted = page.extract_text() or ""
            except Exception as exc:  # pragma: no cover - per-page extraction failure
                logger.debug(
                    "extract_text failed for %s page %d: %s", pdf_path, index + 1, exc
                )
                extracted = ""

            if extracted and extracted.strip():
                page_texts[index] = extracted
                methods[index] = "pypdf2"
            else:
                page_texts[index] = extracted.strip()
                pages_requiring_ocr.append(index)

    target_pages = _compute_target_pages(pdf_path, page_texts)
    pages_requiring_ocr = [idx for idx in pages_requiring_ocr if idx in target_pages]

    if pages_requiring_ocr:
        logger.info("Running OCR for %s page(s) in %s", len(pages_requiring_ocr), pdf_path.name)
        for index in pages_requiring_ocr:
            try:
                kwargs = {"dpi": dpi}
                if POPPLER_PATH:
                    kwargs["poppler_path"] = POPPLER_PATH
                images = convert_from_path(
                    str(pdf_path),
                    first_page=index + 1,
                    last_page=index + 1,
                    **kwargs,
                )
            except PDFInfoNotInstalledError:
                logger.error(
                    "convert_from_path failed for %s: Poppler is required for OCR fallback. "
                    "Install it and set POPPLER_PATH if needed.",
                    pdf_path,
                )
                continue
            except PDFPageCountError as exc:  # pragma: no cover - corrupt PDFs
                logger.error("convert_from_path failed for %s page %d: %s", pdf_path, index + 1, exc)
                continue
            except Exception as exc:  # pragma: no cover - conversion errors
                logger.error("convert_from_path failed for %s page %d: %s", pdf_path, index + 1, exc)
                continue

            if not images:
                continue

            image = images[0]
            processed = ImageOps.grayscale(image)
            processed = ImageOps.autocontrast(processed, cutoff=2)
            processed = processed.filter(ImageFilter.SHARPEN)

            try:
                ocr_text = pytesseract.image_to_string(
                    processed,
                    config="--oem 1 --psm 6 -c preserve_interword_spaces=1",
                )
            except Exception as exc:  # pragma: no cover - OCR dependency issues
                logger.error("Tesseract OCR failed for %s page %d: %s", pdf_path, index + 1, exc)
                continue

            if ocr_text.strip():
                page_texts[index] = ocr_text
                methods[index] = "tesseract"

    ordered_pages = [page_texts[idx] for idx in sorted(page_texts)]
    aggregated = "\n".join(text for text in ordered_pages if text)

    document: DocumentText = {
        "full_text": aggregated,
        "pages": {idx: page_texts[idx] for idx in sorted(page_texts)},
    }
    if methods:
        document["methods"] = methods

    if cache_dir:
        save_to_cache(pdf_path, document, cache_dir)

    return document


def clean_well_name(name: Optional[str]) -> Optional[str]:
    """Remove common form artifacts from well names."""
    if not name:
        return None

    # If "Job Number" appears, extract everything after it
    job_number_match = re.search(r"Job\s+Number\s+(.+)$", name, re.IGNORECASE)
    if job_number_match:
        name = job_number_match.group(1)

    # Remove form labels that leak into extraction
    artifacts = [
        r"^and\s+Number",
        r"^File\s+No\.",
        r"^Well\s+Name",
        r"Report$",
        r"^\s*D\s+",  # Checkbox artifacts
        r"Supplemental\s+History",
        r"^24-HOUR\s+PRODUCTION\s+RATE\s+",  # Table header
        r"^Before\s+After\s+",  # Table column headers
        r".*(?:Directional\s+Survey|Certification\s+Form)\s+",  # Document titles before well name
        r"^.*?(?:Company|LLC|Inc|Corporation|Co\.?)\s+",  # Company names at start
        r"^.*?ND-[A-Z]+\s+-\d+\s+",  # State codes like "ND-SLW -0034"
    ]

    for pattern in artifacts:
        name = re.sub(pattern, "", name, flags=re.IGNORECASE)

    name = name.strip()

    # Well names should contain at least one number
    if not re.search(r'\d', name):
        return None

    return name if len(name) > 2 else None


def fix_operator_ocr_errors(operator: Optional[str]) -> Optional[str]:
    """Fix common OCR errors in operator names using known company dictionary."""
    if not operator:
        return None

    operator_lower = operator.lower()

    # Check for fuzzy match with known operators
    for key, correct_name in KNOWN_OPERATORS.items():
        if key in operator_lower:
            return correct_name

    # Fix common OCR errors manually
    operator = re.sub(r'([A-Z][a-z]+)\s*!\s*([a-z]+)', r'\1p\2', operator)  # ! -> p (Ex!oration -> Exploration)
    operator = re.sub(r'Com\s+an\s+y', 'Company', operator)  # "Com an y" -> "Company"
    operator = re.sub(r'Ex\s+!?\s*oration', 'Exploration', operator)  # "Ex oration" -> "Exploration"
    operator = re.sub(r'\s+', ' ', operator)  # Multiple spaces -> single space

    return operator


def clean_operator(operator: Optional[str]) -> Optional[str]:
    """Remove trailing artifacts from operator names and fix OCR errors."""
    if not operator:
        return None

    # Remove common suffixes that leak into extraction
    operator = re.sub(r'\s+(Rig|Telephone|Address|City|State).*$', '', operator, flags=re.IGNORECASE)
    operator = operator.strip() or None

    # Fix OCR errors
    operator = fix_operator_ocr_errors(operator)

    return operator


def parse_dms_coordinate(text: str, coord_type: str) -> Optional[float]:
    """Parse DMS (Degrees Minutes Seconds) coordinate to decimal."""
    if not text:
        return None

    text = text.strip().replace(',', '.').replace('”', '"')

    hemi_match = re.search(r"(North|South|East|West|N|S|E|W)", text, re.IGNORECASE)
    hemisphere = hemi_match.group(1) if hemi_match else ('N' if coord_type == 'latitude' else 'E')

    value_match = re.search(r"([0-9]{1,3})[°º]\s*([0-9]{1,2})[^0-9]+([0-9]{1,2}(?:\.[0-9]+)?)", text)
    if not value_match:
        value_match = re.search(r"([0-9]{1,3})[°º]\s*([0-9]{1,2})([0-9]{1,2}(?:\.[0-9]+)?)", text)

    if not value_match:
        return None

    degrees, minutes, seconds = value_match.groups()

    try:
        decimal = float(degrees) + float(minutes) / 60.0 + float(seconds) / 3600.0
    except (TypeError, ValueError):
        return None

    if hemisphere.strip().lower() in {'s', 'south', 'w', 'west'}:
        decimal = -decimal

    return decimal


def validate_coordinates(lat: Optional[float], lon: Optional[float], state: Optional[str]) -> Tuple[Optional[float], Optional[float]]:
    """Validate lat/lon are within reasonable bounds for the state."""
    # North Dakota counties (this dataset is all ND wells)
    nd_counties = ["Williams", "McKenzie", "Mountrail", "Dunn", "Divide", "Burke"]

    is_north_dakota = False
    if state:
        state_lower = state.lower()
        is_north_dakota = "north dakota" in state_lower or any(county.lower() in state_lower for county in nd_counties)

    # ND bounds: lat 45.9-49.0, lon -104.05 to -96.55
    # Even if we can't determine state, apply ND logic if coords are in ND range
    if lat and 45.9 <= lat <= 49.0:
        is_north_dakota = True

    if is_north_dakota:
        if lat and not (45.9 <= lat <= 49.0):
            lat = None
        if lon:
            # North Dakota is in western hemisphere, longitude must be negative
            if lon > 0:
                lon = -lon
            if not (-104.05 <= lon <= -96.55):
                lon = None

    return lat, lon


def validate_api_number(api: Optional[str]) -> Optional[str]:
    """Validate and normalize API number format."""
    if not api:
        return None

    # Remove all spaces and dashes for validation
    digits_only = re.sub(r'[^0-9]', '', api)

    # Minimum: state-county-well (10 digits total: 2+3+5)
    if len(digits_only) < 10:
        logger.warning(f"API too short: {api} ({len(digits_only)} digits)")
        return None

    # Extract components: state (2) + county (3) + well number (5+) + optional suffixes
    state = digits_only[:2]
    county = digits_only[2:5]
    well_and_suffixes = digits_only[5:]

    # Normalize well number: strip leading zeros if more than 5 digits in well portion
    # Example: 006055 -> 06055 (strip one leading 0 if well number has 6 digits)
    # Normalize well number: handle OCR errors with extra leading zeros
    # Standard format: 5-digit well number, optionally followed by 2 or 4-digit suffixes
    # If we have 6 digits in well portion and it starts with 0, likely OCR error
    # Examples: 33-053-006055 (11 digits) -> 33-053-06055 (10 digits)
    #           33-053-006055-00 (13 digits) -> 33-053-06055-00 (12 digits)
    if len(digits_only) in [11, 13, 15] and well_and_suffixes[0] == '0':
        # Strip one leading zero from well number
        well_and_suffixes = well_and_suffixes[1:]

    # Reconstruct digits
    digits_only = state + county + well_and_suffixes

    # Strip trailing zero segments (e.g., -00-00 padding)
    if len(digits_only) >= 14 and digits_only[10:14] == "0000":
        digits_only = digits_only[:10]
    elif len(digits_only) >= 12 and digits_only[10:12] == "00":
        digits_only = digits_only[:10]

    # Format based on length
    if len(digits_only) == 10:
        return f"{digits_only[:2]}-{digits_only[2:5]}-{digits_only[5:]}"
    elif len(digits_only) == 12:
        return f"{digits_only[:2]}-{digits_only[2:5]}-{digits_only[5:10]}-{digits_only[10:]}"
    elif len(digits_only) == 14:
        return f"{digits_only[:2]}-{digits_only[2:5]}-{digits_only[5:10]}-{digits_only[10:12]}-{digits_only[12:]}"
    else:
        # Unknown format, return best-effort formatting
        return f"{digits_only[:2]}-{digits_only[2:5]}-{digits_only[5:]}"


def parse_stimulation_table(text: str) -> Optional[Dict[str, Optional[str]]]:
    """Extract stimulation data from tabular format in completion reports."""
    # Pattern: Date | Formation | Top | Bottom | Stages | Volume | Units
    pattern = r"(\d{1,2}/\d{1,2}/\d{4})\s+([A-Za-z\s]+?)\s+(\d+)\s+(\d+)\s+(\d{1,3})\s+([\d,]+(?:\.\d+)?)\s+(Barrels|Gallons|bbls)"

    match = re.search(pattern, text, re.IGNORECASE)
    if match:
        return {
            "date_stimulated": match.group(1),
            "stimulated_formation": match.group(2).strip(),
            "top_ft": match.group(3),
            "bottom_ft": match.group(4),
            "stimulation_stages": match.group(5),
            "volume": match.group(6),
            "volume_units": match.group(7),
        }
    return None


def _coerce_document_text(document: DocumentText | str) -> Tuple[str, Dict[int, str]]:
    if isinstance(document, dict):
        full_text = document.get("full_text", "")
        page_texts = document.get("pages") or {}
    else:
        full_text = str(document)
        page_texts = {0: full_text}
    return full_text, page_texts


def _select_relevant_text(page_texts: Dict[int, str], keywords: Iterable[str], fallback: str) -> str:
    lowered_keywords = [kw.lower() for kw in keywords]
    selected = []
    for index in sorted(page_texts):
        page_text = page_texts[index]
        if not page_text:
            continue
        text_lower = page_text.lower()
        if any(keyword in text_lower for keyword in lowered_keywords):
            selected.append(page_text)
    if selected:
        return "\n".join(selected)
    return fallback


def _convert_dms_to_decimal(degrees: float, minutes: float, seconds: float, hemisphere: str) -> float:
    value = float(degrees) + float(minutes) / 60.0 + float(seconds) / 3600.0
    hemisphere = (hemisphere or "").strip().lower()
    if hemisphere in {"s", "south", "w", "west"}:
        value *= -1.0
    return value


def _extract_dms_coordinate(text: str, coord_type: str) -> Optional[float]:
    if not text:
        return None

    cleaned = text.strip().replace(',', '.').replace('”', '"')
    label = 'latitude' if coord_type == 'lat' else 'longitude'
    pattern = re.compile(
        r"([NSWE])?\s*([0-9]{1,3})[°º]?\s*([0-9]{1,2})[\'′]?\s*([0-9]{1,2}(?:\.[0-9]+)?)\s*(North|South|East|West)?",
        re.IGNORECASE,
    )

    candidates: List[float] = []
    snippets: List[str] = []
    for raw_line in cleaned.splitlines():
        lower = raw_line.lower()
        if label in lower:
            snippets.extend(segment.strip() for segment in raw_line.split(';') if segment.strip())
    if not snippets:
        snippets = [cleaned]

    for snippet in snippets:
        lower_snippet = snippet.lower()
        if coord_type == 'lat' and not any(token in lower_snippet for token in ('north', 'south')):
            continue
        if coord_type == 'lon' and not any(token in lower_snippet for token in ('east', 'west')):
            continue

        for prefix, degrees, minutes, seconds, suffix in pattern.findall(snippet):
            hemisphere = prefix or suffix or ('N' if coord_type == 'lat' else 'E')
            try:
                value = _convert_dms_to_decimal(float(degrees), float(minutes), float(seconds), hemisphere)
            except (TypeError, ValueError):
                continue
            candidates.append(value)

    if not candidates:
        return None

    if coord_type == 'lat':
        for value in candidates:
            if 40.0 <= abs(value) <= 60.0:
                return value
    else:
        for value in candidates:
            if 90.0 <= abs(value) <= 120.0:
                return value

    return candidates[0]


def parse_well_info(text: DocumentText | str) -> Dict[str, Optional[str]]:
    """Parse basic well metadata from extracted text."""

    full_text, page_texts = _coerce_document_text(text)
    subset = _select_relevant_text(
        page_texts,
        (
            "well coordinates",
            "standard report",
            "well name",
            "well data summary",
            "api#",
            "api number",
            "surface hole location",
            "ground level",
            "directional survey",
            "surface latitude",
            "surface longitude",
        ),
        full_text,
    )

    lines_normalized = normalise_text(subset)
    data = {key: extract_first_match(lines_normalized, patterns) for key, patterns in WELL_PATTERNS.items()}

    # Latitude / longitude often appear together on the same line.
    lat_long_match = re.search(
        r"Latitude[:#\s-]+(-?\d+\.\d+).{0,40}?Longitude[:#\s-]+(-?\d+\.\d+)",
        lines_normalized,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if lat_long_match:
        data["latitude"] = lat_long_match.group(1)
        data["longitude"] = lat_long_match.group(2)

    if not data.get("api"):
        fallback_api = extract_api_fallback(lines_normalized)
        if fallback_api:
            data["api"] = fallback_api

    # Clean API number - remove leading garbage digits if present (e.g., "2633-053-06025" -> "33-053-06025")
    if data.get("api"):
        api_cleaned = data["api"]
        # If API has leading digits before ND prefix (33), extract just the ND part
        nd_match = re.search(r'(33-\d{3}-\d{5}(?:-\d{2})?(?:-\d{2})?)', api_cleaned)
        if nd_match:
            data["api"] = nd_match.group(1)

    # Apply cleaning and validation
    operator = clean_operator(clean_string(data.get("operator")))
    well_name = clean_well_name(clean_string(data.get("well_name")))
    county_state = clean_string(data.get("county_state"))

    # Validate and correct coordinates
    # Try DMS format first, then fall back to decimal
    lat_text = data.get("latitude")
    lon_text = data.get("longitude")

    lat = parse_dms_coordinate(lat_text, "latitude") if lat_text else None
    if lat is None:
        lat = safe_float(lat_text)
    if lat is None:
        lat_matches = [segment.strip() for segment in re.findall(r"Latitude\s+([^;\n]+)", lines_normalized, re.IGNORECASE) if '°' in segment or 'deg' in segment.lower()]
        for candidate in lat_matches:
            parsed = parse_dms_coordinate(candidate, 'latitude')
            if parsed is not None:
                lat = parsed
                break
    if lat is None:
        lat = _extract_dms_coordinate(lines_normalized, 'lat')

    lon = parse_dms_coordinate(lon_text, 'longitude') if lon_text else None
    if lon is None:
        lon = safe_float(lon_text)
    if lon is None:
        lon_matches = [segment.strip() for segment in re.findall(r"Longitude\s+([^;\n]+)", lines_normalized, re.IGNORECASE) if '°' in segment or 'deg' in segment.lower()]
        for candidate in lon_matches:
            parsed = parse_dms_coordinate(candidate, 'longitude')
            if parsed is not None:
                lon = parsed
                break
    if lon is None:
        lon = _extract_dms_coordinate(lines_normalized, 'lon')

    lat, lon = validate_coordinates(lat, lon, county_state)

    # Validate and normalize API number (CRITICAL for web scraping)
    api = validate_api_number(normalise_api_string(data.get("api")))

    return {
        "operator": limit_length(operator, 255),
        "well_name": limit_length(well_name, 255),
        "api": limit_length(api, 64),
        "enseco_job": limit_length(clean_string(data.get("enseco_job")), 64),
        "job_type": limit_length(clean_string(data.get("job_type")), 255),
        "county_state": limit_length(county_state, 255),
        "shl": clean_string(data.get("shl")),
        "latitude": lat,
        "longitude": lon,
        "datum": limit_length(clean_string(data.get("datum")), 255),
    }


def parse_stimulation_data(text: DocumentText | str) -> Dict[str, Optional[str]]:
    """Parse stimulation information from extracted text."""

    full_text, page_texts = _coerce_document_text(text)
    subset = _select_relevant_text(
        page_texts,
        (
            "stimulated formation",
            "stimulation stages",
            "type treatment",
            "lbs proppant",
            "details",
            "date stimulated",
        ),
        full_text,
    )

    lines_normalized = normalise_text(subset)

    # Try table format first (common in completion reports)
    table_data = parse_stimulation_table(lines_normalized)
    if table_data:
        # Merge with individual pattern extraction for fields not in table
        data = {key: extract_first_match(lines_normalized, patterns)
                for key, patterns in STIM_PATTERNS.items()}
        # Table data takes precedence for fields it contains
        for key, value in table_data.items():
            if value:
                data[key] = value
    else:
        data = {key: extract_first_match(lines_normalized, patterns)
                for key, patterns in STIM_PATTERNS.items()}

    details = extract_multiline_block(lines_normalized, "Details") or data.get("details")

    # Clean formation name
    formation = clean_string(data.get("stimulated_formation"))
    if formation:
        # Filter out table headers
        if "Top" in formation or "Bottom" in formation or "|" in formation or "Stimulation Stages" in formation:
            formation = None

    # Validate treatment type
    treatment = clean_string(data.get("type_treatment"))
    if treatment:
        # Filter out table headers
        if "Acid%" in treatment or "Lbs Proppant" in treatment or "|" in treatment or "Maximum Treatment" in treatment:
            treatment = None

    # Clean acid field
    acid = clean_string(data.get("acid"))
    if acid:
        # Filter out table headers
        if "Lbs Proppant" in acid or "Maximum Treatment" in acid or "|" in acid:
            acid = None

    return {
        "date_stimulated": safe_date(data.get("date_stimulated")),
        "stimulated_formation": limit_length(formation, 255),
        "top_ft": safe_float(data.get("top_ft")),
        "bottom_ft": safe_float(data.get("bottom_ft")),
        "stimulation_stages": safe_int(data.get("stimulation_stages")),
        "volume": safe_float(data.get("volume")),
        "volume_units": limit_length(clean_string(data.get("volume_units")), 32),
        "type_treatment": limit_length(treatment, 255),
        "acid": limit_length(acid, 255),
        "lbs_proppant": safe_float(data.get("lbs_proppant")),
        "max_treatment_pressure": safe_float(data.get("max_treatment_pressure")),
        "max_treatment_rate": safe_float(data.get("max_treatment_rate")),
        "details": limit_length(clean_string(details), 65500),
    }


def insert_data(session, well_data: Dict[str, Optional[str]], stim_data: Dict[str, Optional[str]], source_path: Path) -> None:
    """Upsert the parsed data into the database."""

    well_prepared = apply_missing_defaults(
        well_data,
        string_fields={"operator", "well_name", "enseco_job", "job_type", "county_state", "shl", "datum"},
        numeric_fields={"latitude", "longitude"},
        exclude={"api"},
    )
    well_payload = {k: v for k, v in well_prepared.items() if v not in (None, "")}
    if not well_payload.get("api"):
        logger.warning("Skipping %s because no API number was parsed", source_path)
        return

    well = session.query(Well).filter(Well.api == well_payload["api"]).one_or_none()
    if well is None:
        well = Well(**well_payload)
        session.add(well)
        session.flush()
        logger.info("Inserted new well %s from %s", well.api, source_path.name)
    else:
        for key, value in well_payload.items():
            setattr(well, key, value)
        logger.info("Updated existing well %s from %s", well.api, source_path.name)

    stim_prepared = apply_missing_defaults(
        stim_data,
        string_fields={"stimulated_formation", "volume_units", "type_treatment", "acid", "details"},
        numeric_fields={
            "top_ft",
            "bottom_ft",
            "stimulation_stages",
            "volume",
            "lbs_proppant",
            "max_treatment_pressure",
            "max_treatment_rate",
        },
        exclude={"date_stimulated"},
    )
    stim_payload = {k: v for k, v in stim_prepared.items() if v not in (None, "")}
    if stim_payload:
        existing = None
        if stim_payload.get("date_stimulated"):
            existing = (
                session.query(StimulationData)
                .filter(
                    StimulationData.well_id == well.id,
                    StimulationData.date_stimulated == stim_payload["date_stimulated"],
                )
                .one_or_none()
            )
        if existing is None:
            session.add(StimulationData(well=well, **stim_payload))
        else:
            for key, value in stim_payload.items():
                setattr(existing, key, value)

    session.commit()


def process_pdf(session, pdf_path: Path, cache_dir: Optional[Path] = None,
                use_cache: bool = True, rebuild_cache: bool = False) -> Tuple[Dict[str, Optional[str]], Dict[str, Optional[str]]]:
    # Extract text using PyPDF2 + selective Tesseract
    document_text = extract_text_from_pdf(
        pdf_path,
        cache_dir=cache_dir,
        use_cache=use_cache,
        rebuild_cache=rebuild_cache,
    )
    if not document_text.get("full_text", "").strip():
        logger.warning("%s produced no extractable text", pdf_path)
        return {}, {}

    # Parse well data from initial extraction
    well_data = parse_well_info(document_text)

    # Validate extraction quality and retry with Tesseract if needed
    pages_needing_retry = identify_pages_for_retry(pdf_path, well_data, document_text)

    if pages_needing_retry:
        logger.info(f"{pdf_path.name}: Retrying {len(pages_needing_retry)} pages with Tesseract due to validation failures")
        # Re-OCR problematic pages with Tesseract
        document_text = retry_pages_with_tesseract(pdf_path, pages_needing_retry, document_text)

        # Re-parse well data with improved text
        well_data = parse_well_info(document_text)

        # Update cache with corrected extraction
        if cache_dir:
            save_to_cache(pdf_path, document_text, cache_dir)
            logger.debug(f"Updated cache for {pdf_path.name} with Tesseract-corrected pages")

    stim_data = parse_stimulation_data(document_text)

    insert_data(session, well_data, stim_data, pdf_path)
    return well_data, stim_data


def main(pdf_folder: str = "./pdfs", cache_dir: Optional[str] = None,
         use_cache: bool = True, rebuild_cache: bool = False) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    folder = Path(pdf_folder).expanduser().resolve()
    if not folder.exists():
        raise FileNotFoundError(f"PDF folder not found: {folder}")

    # Set up cache directory
    cache_path = None
    if cache_dir:
        cache_path = Path(cache_dir).expanduser().resolve()
        logger.info("Using cache directory: %s", cache_path)
        if rebuild_cache:
            logger.info("Rebuild cache mode: will force re-extraction")
        elif not use_cache:
            logger.info("Cache disabled: will not read from cache")

    pdf_files = sorted(p for p in folder.rglob("*.pdf") if p.is_file())
    if not pdf_files:
        logger.warning("No PDF files found in %s", folder)
        return

    session = get_session()
    try:
        for pdf_path in pdf_files:
            logger.info("Processing %s", pdf_path)
            process_pdf(session, pdf_path, cache_dir=cache_path, use_cache=use_cache, rebuild_cache=rebuild_cache)
    finally:
        session.close()


def extract_first_match(text: str, patterns: Iterable[str]) -> Optional[str]:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match and match.group(1):
            return match.group(1).strip()
    return None


def extract_multiline_block(text: str, label: str) -> Optional[str]:
    pattern = rf"{re.escape(label)}[:#\s-]+(.+?)(?=\n[A-Z][^\n]{0,40}[:#\s-]|\Z)"
    match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
    if match:
        return match.group(1).strip()
    return None


def normalise_text(text: str) -> str:
    return re.sub(r"\r", "", text)


def clean_string(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    unescaped = html.unescape(value)
    without_tags = HTML_TAG_RE.sub(" ", unescaped)
    without_controls = re.sub(r"[\r\n\t]+", " ", without_tags)
    without_specials = NON_PRINTABLE_RE.sub(" ", without_controls)
    cleaned = re.sub(r"\s+", " ", without_specials).strip()
    return cleaned or None


def normalise_api_string(value: Optional[str]) -> Optional[str]:
    cleaned = clean_string(value)
    if cleaned is None:
        return None
    cleaned = cleaned.replace("–", "-").replace("—", "-")
    cleaned = re.sub(r"\s+", "", cleaned)
    cleaned = re.sub(r"[^0-9A-Za-z-]", "", cleaned)
    return cleaned or None


def safe_float(value: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value.replace(",", "").strip())
    except Exception:
        return None


def safe_int(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(float(value.replace(",", "").strip()))
    except Exception:
        return None


def safe_date(value: Optional[str]) -> Optional[datetime.date]:
    if value is None:
        return None
    value = value.strip()
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def limit_length(value: Optional[str], max_length: int) -> Optional[str]:
    if value is None:
        return None
    if len(value) <= max_length:
        return value
    return value[:max_length]


def apply_missing_defaults(
    data: Dict[str, Optional[str]],
    *,
    string_fields: Iterable[str],
    numeric_fields: Iterable[str],
    exclude: Optional[Iterable[str]] = None,
) -> Dict[str, Optional[str]]:
    """Replace missing values with standard defaults before persistence."""

    exclude_set = set(exclude or [])
    updated = dict(data)

    for field in string_fields:
        if field in exclude_set:
            continue
        if updated.get(field) in (None, ""):
            updated[field] = STRING_MISSING_DEFAULT

    for field in numeric_fields:
        if field in exclude_set:
            continue
        value = updated.get(field)
        if value is None or value == "":
            updated[field] = NUMERIC_MISSING_DEFAULT

    return updated


def extract_api_fallback(text: str) -> Optional[str]:
    """Attempt to recover an API number even when formatting is irregular."""

    def format_api(digits: str) -> Optional[str]:
        length = len(digits)
        if length == 10:
            return f"{digits[:2]}-{digits[2:5]}-{digits[5:]}"
        if length == 12:
            return f"{digits[:2]}-{digits[2:5]}-{digits[5:10]}-{digits[10:]}"
        if length == 14:
            return f"{digits[:2]}-{digits[2:5]}-{digits[5:10]}-{digits[10:12]}-{digits[12:]}"
        return None

    normalised = text.replace("\u2013", "-").replace("\u2014", "-")
    pattern = re.compile(r"(?:\d[\s\-/\\]*){10,14}")

    candidates = []
    for match in pattern.finditer(normalised):
        digits = re.sub(r"\D", "", match.group(0))
        if 10 <= len(digits) <= 14:
            candidates.append(digits)

    contiguous = re.findall(r"\b\d{10,14}\b", normalised)
    candidates.extend(contiguous)

    if not candidates:
        return None

    # Deduplicate while preserving first occurrence order then prefer longer matches.
    seen = []
    ordered = []
    for value in candidates:
        if value not in seen:
            seen.append(value)
            ordered.append(value)
    ordered.sort(key=len, reverse=True)
    for digits in ordered:
        formatted = format_api(digits)
        if formatted:
            return formatted

    return None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Parse well PDFs and populate the database",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Standard run with cache enabled (default location: ./ocr_cache)
  python pdf_parser.py ./pdfs --cache-dir ./ocr_cache

  # Force rebuild all cached OCR results
  python pdf_parser.py ./pdfs --cache-dir ./ocr_cache --rebuild-cache

  # Disable cache completely
  python pdf_parser.py ./pdfs --no-cache
        """
    )
    parser.add_argument("pdf_folder", nargs="?", default="./pdfs",
                        help="Folder containing PDF files (default: ./pdfs)")
    parser.add_argument("--cache-dir", type=str, default="./ocr_cache",
                        help="Directory to store/load OCR cache (default: ./ocr_cache)")
    parser.add_argument("--no-cache", action="store_true",
                        help="Disable reading from cache (will still write to cache)")
    parser.add_argument("--rebuild-cache", action="store_true",
                        help="Force re-extraction even if cache exists")

    args = parser.parse_args()

    main(
        pdf_folder=args.pdf_folder,
        cache_dir=args.cache_dir,
        use_cache=not args.no_cache,
        rebuild_cache=args.rebuild_cache
    )
