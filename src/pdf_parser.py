import os
import re
from datetime import datetime
from PyPDF2 import PdfReader
import pytesseract
from pdf2image import convert_from_path
from db_utils import get_session, Well, StimulationData

# ========== PDF 文本提取 ==========
def extract_text_from_pdf(pdf_path):
    text = ""
    try:
        reader = PdfReader(pdf_path)
        for page in reader.pages:
            text += page.extract_text() or ""
    except Exception:
        text = ""

    if not text.strip():
        images = convert_from_path(pdf_path)
        for img in images:
            text += pytesseract.image_to_string(img)

    return text

# ========== 解析油井基本信息 ==========
def parse_well_info(text):
    data = {}

    data["operator"] = search_value(r"Operator[:\s]+(.+)", text)
    data["well_name"] = search_value(r"Well Name[:\s]+(.+)", text)
    data["api"] = search_value(r"API[:\s]+([\d\-]+)", text)
    data["enseco_job"] = search_value(r"Enseco Job #[:\s]+(\S+)", text)
    data["job_type"] = search_value(r"Job Type[:\s]+(.+)", text)
    data["county_state"] = search_value(r"County, State[:\s]+(.+)", text)
    data["shl"] = search_value(r"Surface Hole Location \(SHL\)[:\s]+(.+)", text)
    data["latitude"] = safe_float(search_value(r"Latitude[:\s]+([-]?\d+\.\d+)", text))
    data["longitude"] = safe_float(search_value(r"Longitude[:\s]+([-]?\d+\.\d+)", text))
    data["datum"] = search_value(r"Datum[:\s]+(.+)", text)

    return data

# ========== 解析刺激数据 ==========
def parse_stimulation_data(text):
    stim = {}

    stim["date_stimulated"] = safe_date(search_value(r"Date Stimulated[:\s]+(.+)", text))
    stim["stimulated_formation"] = search_value(r"Stimulated Formation[:\s]+(.+)", text)
    stim["top_ft"] = safe_float(search_value(r"Top\s*\(ft\)[:\s]+(\d+)", text))
    stim["bottom_ft"] = safe_float(search_value(r"Bottom\s*\(ft\)[:\s]+(\d+)", text))
    stim["stimulation_stages"] = safe_int(search_value(r"Stimulation Stages[:\s]+(\d+)", text))
    stim["volume"] = safe_float(search_value(r"Volume[:\s]+([\d\.]+)", text))
    stim["volume_units"] = search_value(r"Volume Units[:\s]+(\w+)", text)
    stim["type_treatment"] = search_value(r"Type Treatment[:\s]+(.+)", text)
    stim["acid"] = search_value(r"Acid[:\s]+(.+)", text)
    stim["lbs_proppant"] = safe_float(search_value(r"Lbs Proppant[:\s]+([\d,]+)", text))
    stim["max_treatment_pressure"] = safe_float(search_value(r"Maximum Treatment Pressure.*?(\d+)", text))
    stim["max_treatment_rate"] = safe_float(search_value(r"Maximum Treatment Rate.*?([\d\.]+)", text))
    stim["details"] = search_value(r"Details[:\s]+(.+)", text)

    return stim

# ========== 工具函数 ==========
def search_value(pattern, text):
    match = re.search(pattern, text, re.IGNORECASE)
    return match.group(1).strip() if match else None

def safe_float(val):
    try:
        return float(val.replace(",", "")) if val else None
    except Exception:
        return None

def safe_int(val):
    try:
        return int(val) if val else None
    except Exception:
        return None

def safe_date(val):
    try:
        return datetime.strptime(val, "%m/%d/%Y").date()
    except Exception:
        return None

# ========== 存数据库 ==========
def insert_data(session, well_data, stim_data):
    well = Well(**well_data)
    session.add(well)
    session.commit()

    stim = StimulationData(**stim_data, well_id=well.id)
    session.add(stim)
    session.commit()

# ========== 主函数 ==========
def main(pdf_folder="./pdfs"):
    session = get_session()

    for filename in os.listdir(pdf_folder):
        if filename.endswith(".pdf"):
            pdf_path = os.path.join(pdf_folder, filename)
            print(f"📂 Processing {pdf_path} ...")
            text = extract_text_from_pdf(pdf_path)

            well_data = parse_well_info(text)
            stim_data = parse_stimulation_data(text)

            print("🔎 Well Info:", well_data)
            print("🔎 Stimulation Data:", stim_data)

            insert_data(session, well_data, stim_data)

    session.close()

if __name__ == "__main__":
    main()
