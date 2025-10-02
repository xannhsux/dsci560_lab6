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
import html
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple

from PyPDF2 import PdfReader
from pdf2image import convert_from_path
from pdf2image.exceptions import PDFInfoNotInstalledError, PDFPageCountError
import pytesseract

if __package__ in (None, ""):
    import sys

    sys.path.append(str(Path(__file__).resolve().parent))
    from db_utils import Well, StimulationData, get_session
else:  # pragma: no cover - executed when imported as package module
    from .db_utils import Well, StimulationData, get_session


logger = logging.getLogger(__name__)


POPPLER_PATH = os.getenv("POPPLER_PATH")
TESSERACT_CMD = os.getenv("TESSERACT_CMD")
if TESSERACT_CMD:
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD


WELL_PATTERNS = {
    "operator": [r"Operator(?: Name)?[:#\s-]+(.+)", r"Operator\s+(.*)",],
    "well_name": [
        r"Well(?: Name)?(?: & Number)?[:#\s-]+(.+)",
        r"Well\s+Name\s*/\s*Number[:#\s-]+(.+)",
    ],
    "api": [
        r"API(?:\s*Number|\s*No\.?|\s*#)?[:#\s-]*([0-9\-]{5,})",
        r"API(?:\s*Number|\s*No\.?|\s*#)?[:#\s-]*([0-9\s\-]{5,})",
    ],
    "enseco_job": [r"Enseco\s*Job\s*#[:#\s-]+(\S+)"],
    "job_type": [r"Job\s*Type[:#\s-]+(.+)", r"Type of Job[:#\s-]+(.+)",],
    "county_state": [r"County,?\s*State[:#\s-]+(.+)", r"County[:#\s-]+(.+)"],
    "shl": [r"Surface\s*Hole\s*Location\s*\(SHL\)[:#\s-]+(.+)"],
    "latitude": [
        r"Latitude[:#\s-]+(-?\d+\.\d+)",
        r"Lat(?:itude)?[:#\s-]+(-?\d+\.\d+)",
    ],
    "longitude": [
        r"Longitude[:#\s-]+(-?\d+\.\d+)",
        r"Long(?:itude)?[:#\s-]+(-?\d+\.\d+)",
    ],
    "datum": [r"Datum[:#\s-]+(.+)"]
}


STIM_PATTERNS = {
    "date_stimulated": [r"Date\s*Stimulated[:#\s-]+(.+)", r"Stimulated\s*Date[:#\s-]+(.+)"],
    "stimulated_formation": [r"Stimulated\s*Formation[:#\s-]+(.+)", r"Formation[:#\s-]+(.+)"],
    "top_ft": [r"Top\s*\(ft\)[:#\s-]+([\d,]+)", r"Top[:#\s-]+([\d,]+)\s*ft"],
    "bottom_ft": [r"Bottom\s*\(ft\)[:#\s-]+([\d,]+)", r"Bottom[:#\s-]+([\d,]+)\s*ft"],
    "stimulation_stages": [r"Stimulation\s*Stages[:#\s-]+(\d+)", r"Stages[:#\s-]+(\d+)"],
    "volume": [
        r"Volume\s*\(?(?:bbls|gal|m3)?\)?[:#\s-]+([\d,]+(?:\.\d+)?)",
        r"Total\s*Volume[:#\s-]+([\d,]+(?:\.\d+)?)",
    ],
    "volume_units": [
        r"Volume\s*(?:\(([^)]+)\))",
        r"Volume\s*Units[:#\s-]+(\w+)",
    ],
    "type_treatment": [r"Type\s*Treatment[:#\s-]+(.+)", r"Treatment\s*Type[:#\s-]+(.+)",],
    "acid": [r"Acid[:#\s-]+(.+)", r"Acid\s*Type[:#\s-]+(.+)"],
    "lbs_proppant": [r"Lbs?\.?\s*Proppant[:#\s-]+([\d,]+)", r"Proppant[:#\s-]+([\d,]+)",],
    "max_treatment_pressure": [r"Max(?:imum)?\s*Treatment\s*Pressure[:#\s-]+([\d,]+)",],
    "max_treatment_rate": [r"Max(?:imum)?\s*Treatment\s*Rate[:#\s-]+([\d,]+(?:\.\d+)?)",],
    "details": [r"Details[:#\s-]+(.+)"]
}


HTML_TAG_RE = re.compile(r"<[^>]+>")
NON_PRINTABLE_RE = re.compile(r"[^\x09\x0A\x0D\x20-\x7E]")
STRING_MISSING_DEFAULT = "N/A"
NUMERIC_MISSING_DEFAULT = 0


def extract_text_from_pdf(pdf_path: Path, dpi: int = 300) -> str:
    """Return textual content from a PDF file.

    We first try to read the embedded text.  If the document is image-only we
    fall back to OCR via ``pdf2image`` and ``pytesseract``.
    """

    text_chunks = []
    try:
        reader = PdfReader(str(pdf_path))
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.warning("Failed to open text layer for %s: %s", pdf_path, exc)
        reader = None

    if reader is not None:
        for page_number, page in enumerate(reader.pages, start=1):
            try:
                extracted = page.extract_text() or ""
            except Exception as exc:  # pragma: no cover - per-page extraction failure
                logger.debug(
                    "extract_text failed for %s page %d: %s", pdf_path, page_number, exc
                )
                continue
            if extracted.strip():
                text_chunks.append(extracted)

    aggregated = "\n".join(text_chunks)
    if aggregated.strip():
        return aggregated

    logger.info("Falling back to OCR for %s", pdf_path)
    try:
        kwargs = {"dpi": dpi}
        if POPPLER_PATH:
            kwargs["poppler_path"] = POPPLER_PATH
        images = convert_from_path(str(pdf_path), **kwargs)
    except PDFInfoNotInstalledError:
        logger.error(
            "convert_from_path failed for %s: Poppler is required for OCR fallback. "
            "Install it and set POPPLER_PATH if needed.",
            pdf_path,
        )
        return ""
    except PDFPageCountError as exc:  # pragma: no cover - corrupt PDFs
        logger.error("convert_from_path failed for %s: %s", pdf_path, exc)
        return ""
    except Exception as exc:  # pragma: no cover - conversion errors
        logger.error("convert_from_path failed for %s: %s", pdf_path, exc)
        return ""

    ocr_text = []
    for image in images:
        try:
            ocr_text.append(pytesseract.image_to_string(image))
        except Exception as exc:  # pragma: no cover - OCR dependency issues
            logger.error("Tesseract OCR failed for %s: %s", pdf_path, exc)
            return ""

    return "\n".join(ocr_text)


def parse_well_info(text: str) -> Dict[str, Optional[str]]:
    """Parse basic well metadata from extracted text."""

    lines_normalized = normalise_text(text)
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

    return {
        "operator": limit_length(clean_string(data.get("operator")), 255),
        "well_name": limit_length(clean_string(data.get("well_name")), 255),
        "api": limit_length(clean_string(normalise_api_string(data.get("api"))), 64),
        "enseco_job": limit_length(clean_string(data.get("enseco_job")), 64),
        "job_type": limit_length(clean_string(data.get("job_type")), 255),
        "county_state": limit_length(clean_string(data.get("county_state")), 255),
        "shl": clean_string(data.get("shl")),
        "latitude": safe_float(data.get("latitude")),
        "longitude": safe_float(data.get("longitude")),
        "datum": limit_length(clean_string(data.get("datum")), 255),
    }


def parse_stimulation_data(text: str) -> Dict[str, Optional[str]]:
    """Parse stimulation information from extracted text."""

    lines_normalized = normalise_text(text)
    data = {key: extract_first_match(lines_normalized, patterns) for key, patterns in STIM_PATTERNS.items()}

    details = extract_multiline_block(lines_normalized, "Details") or data.get("details")

    return {
        "date_stimulated": safe_date(data.get("date_stimulated")),
        "stimulated_formation": limit_length(clean_string(data.get("stimulated_formation")), 255),
        "top_ft": safe_float(data.get("top_ft")),
        "bottom_ft": safe_float(data.get("bottom_ft")),
        "stimulation_stages": safe_int(data.get("stimulation_stages")),
        "volume": safe_float(data.get("volume")),
        "volume_units": limit_length(clean_string(data.get("volume_units")), 32),
        "type_treatment": limit_length(clean_string(data.get("type_treatment")), 255),
        "acid": limit_length(clean_string(data.get("acid")), 255),
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


def process_pdf(session, pdf_path: Path) -> Tuple[Dict[str, Optional[str]], Dict[str, Optional[str]]]:
    text = extract_text_from_pdf(pdf_path)
    if not text.strip():
        logger.warning("%s produced no extractable text", pdf_path)
        return {}, {}

    well_data = parse_well_info(text)
    stim_data = parse_stimulation_data(text)

    insert_data(session, well_data, stim_data, pdf_path)
    return well_data, stim_data


def main(pdf_folder: str = "./pdfs") -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    folder = Path(pdf_folder).expanduser().resolve()
    if not folder.exists():
        raise FileNotFoundError(f"PDF folder not found: {folder}")

    pdf_files = sorted(p for p in folder.rglob("*.pdf") if p.is_file())
    if not pdf_files:
        logger.warning("No PDF files found in %s", folder)
        return

    session = get_session()
    try:
        for pdf_path in pdf_files:
            logger.info("Processing %s", pdf_path)
            process_pdf(session, pdf_path)
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
    parser = argparse.ArgumentParser(description="Parse well PDFs and populate the database")
    parser.add_argument("pdf_folder", nargs="?", default="./pdfs", help="Folder containing PDF files")
    args = parser.parse_args()
    main(args.pdf_folder)
