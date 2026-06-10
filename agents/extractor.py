"""
Extractor Agent — robust, free, local-first extraction
Transplanted text extraction stack from CA pipeline (pdfplumber → PyMuPDF → pdfminer → OCR).

Flow:
  PDF  → pdfplumber / PyMuPDF / pdfminer (text layer, fastest)
       → PyMuPDF page-render + Tesseract OCR (scanned PDFs, no poppler needed)
  Image → Tesseract OCR (with image preprocessing)
  
  Extracted text → Groq LLM (free, fast, generous limits)
  Last resort    → Vision LLM if text extraction fully fails
"""

import os
import re
import sys
import json
import shutil
import tempfile
from pathlib import Path
from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential

from llm.client import get_llm, build_vision_message

load_dotenv()

CONFIDENCE_LOW   = float(os.getenv("CONFIDENCE_THRESHOLD_LOW", 0.6))
TEXT_MIN_CHARS   = int(os.getenv("TEXT_MIN_CHARS", 80))
VISION_LLM_RETRIES = int(os.getenv("VISION_LLM_RETRIES", 2))

REQUIRED_FIELDS = [
    "consignee_name",
    "hs_code",
    "port_of_loading",
    "port_of_discharge",
    "incoterms",
    "description_of_goods",
    "gross_weight",
    "invoice_number",
]

EXTRACTION_PROMPT = """You are a trade document extraction specialist.
Extract the following fields from this trade document exactly as they appear.

Fields to extract:
- consignee_name: The name of the consignee (receiver of goods)
- hs_code: Harmonized System (HS) code for the goods
- port_of_loading: Port where goods were loaded
- port_of_discharge: Port where goods will be discharged/unloaded
- incoterms: Trade terms (e.g. FOB, CIF, EXW, DDP, CFR)
- description_of_goods: Description of the goods being shipped
- gross_weight: Total gross weight including packaging
- invoice_number: Commercial invoice number

Rules:
1. Extract ONLY what is explicitly present in the document. Do NOT infer or guess.
2. If a field is not found, set value to null.
3. For confidence:
   - 0.9-1.0: Field is clearly visible and unambiguous
   - 0.7-0.89: Field is present but slightly unclear or abbreviated
   - 0.5-0.69: Field may be present but uncertain (bad scan, partial text)
   - 0.0-0.49: Field not found or extremely unclear

Respond ONLY with valid JSON in this exact format:
{
  "consignee_name": {"value": "...", "confidence": 0.0},
  "hs_code": {"value": "...", "confidence": 0.0},
  "port_of_loading": {"value": "...", "confidence": 0.0},
  "port_of_discharge": {"value": "...", "confidence": 0.0},
  "incoterms": {"value": "...", "confidence": 0.0},
  "description_of_goods": {"value": "...", "confidence": 0.0},
  "gross_weight": {"value": "...", "confidence": 0.0},
  "invoice_number": {"value": "...", "confidence": 0.0}
}

No explanation. No markdown. Only the JSON object.
"""


# ══════════════════════════════════════════════════════════════════════════════
# OPTIONAL IMPORTS — graceful fallback if not installed
# ══════════════════════════════════════════════════════════════════════════════

try:
    import pdfplumber
    HAS_PDFPLUMBER = True
except ImportError:
    HAS_PDFPLUMBER = False

try:
    import fitz  # PyMuPDF
    HAS_FITZ = True
except ImportError:
    HAS_FITZ = False

try:
    from pdfminer.high_level import extract_text as _pdfminer_extract
    HAS_PDFMINER = True
except ImportError:
    HAS_PDFMINER = False

try:
    import pytesseract
    from PIL import Image, ImageEnhance, ImageFilter
    HAS_OCR = True
except ImportError:
    HAS_OCR = False


def _alphanum_len(text: str) -> int:
    """Count alphanumeric chars — used to judge extraction quality."""
    return len(re.sub(r"[^a-zA-Z0-9]", "", text or ""))


# ══════════════════════════════════════════════════════════════════════════════
# TESSERACT AUTO-DETECTION (stolen from CA pipeline — handles all OS paths)
# ══════════════════════════════════════════════════════════════════════════════

def _find_tesseract() -> bool:
    """Auto-detect Tesseract on Windows, Mac, Linux. No manual PATH setup needed."""
    if not HAS_OCR:
        return False
    try:
        pytesseract.get_tesseract_version()
        return True  # already on PATH
    except Exception:
        pass

    import glob
    candidates = []
    if sys.platform == "win32":
        candidates += glob.glob(r"C:\Users\*\AppData\Local\Programs\Tesseract-OCR\tesseract.exe")
        candidates += [
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"),
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
            r"C:\Tesseract-OCR\tesseract.exe",
        ]
    else:
        candidates += [
            "/usr/bin/tesseract",
            "/usr/local/bin/tesseract",
            "/opt/homebrew/bin/tesseract",
        ]

    for path in candidates:
        if os.path.isfile(path):
            pytesseract.pytesseract.tesseract_cmd = path
            try:
                pytesseract.get_tesseract_version()
                print(f"[Extractor] Found Tesseract at: {path}")
                return True
            except Exception:
                continue
    print("[Extractor] Tesseract not found. Install from https://github.com/UB-Mannheim/tesseract/wiki")
    return False


_TESSERACT_OK = _find_tesseract()


# ══════════════════════════════════════════════════════════════════════════════
# TEXT EXTRACTION LAYER
# ══════════════════════════════════════════════════════════════════════════════

def _extract_pdfplumber(path: str) -> str:
    if not HAS_PDFPLUMBER:
        return ""
    try:
        parts = []
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                t = page.extract_text(layout=True)
                if t:
                    parts.append(t)
                else:
                    words = page.extract_words()
                    if words:
                        parts.append(" ".join(w["text"] for w in words))
        return "\n\n".join(parts).strip()
    except Exception:
        return ""


def _extract_pymupdf(path: str) -> str:
    if not HAS_FITZ:
        return ""
    try:
        doc = fitz.open(path)
        parts = [page.get_text("text") for page in doc]
        doc.close()
        return "\n\n".join(parts).strip()
    except Exception:
        return ""


def _extract_pdfminer(path: str) -> str:
    if not HAS_PDFMINER:
        return ""
    try:
        return (_pdfminer_extract(path) or "").strip()
    except Exception:
        return ""


def _preprocess_image(img):
    """Mild preprocessing — enhance contrast/sharpness for better OCR."""
    img = img.convert("L")
    img = ImageEnhance.Contrast(img).enhance(1.5)
    img = ImageEnhance.Sharpness(img).enhance(2.0)
    return img


def _ocr_image_file(path: str) -> str:
    """OCR a standalone image file."""
    if not _TESSERACT_OK:
        return ""
    try:
        with Image.open(path) as img:
            proc = _preprocess_image(img)
            return pytesseract.image_to_string(proc, lang="eng", config="--psm 6 --oem 3")
    except Exception as e:
        print(f"[Extractor] Image OCR failed: {e}")
        return ""


def _ocr_pdf(path: str) -> str:
    """
    Render each PDF page to 300 DPI image using PyMuPDF, then OCR.
    Does NOT require poppler or pdf2image — pure Python.
    """
    if not HAS_FITZ or not _TESSERACT_OK:
        return ""
    pages_text = []
    tmpdir = tempfile.mkdtemp(prefix="trade_ocr_")
    try:
        doc = fitz.open(path)
        for page_num, page in enumerate(doc):
            mat = fitz.Matrix(300 / 72, 300 / 72)  # 300 DPI
            pix = page.get_pixmap(matrix=mat, alpha=False)
            tmp_img = os.path.join(tmpdir, f"page_{page_num}.png")
            pix.save(tmp_img)
            with Image.open(tmp_img) as img:
                proc = _preprocess_image(img)
                t = pytesseract.image_to_string(proc, lang="eng", config="--psm 6 --oem 3")
                if t.strip():
                    pages_text.append(t)
        doc.close()
    except Exception as e:
        print(f"[Extractor] PDF OCR failed: {e}")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    return "\n\n".join(pages_text)


def extract_text(filepath: str) -> tuple[str, str]:
    """
    Extract text from PDF or image.
    Returns (text, method_used).
    
    For PDFs: pdfplumber → PyMuPDF → pdfminer → PyMuPDF+Tesseract OCR
    For images: Tesseract OCR directly
    """
    ext = Path(filepath).suffix.lower()

    if ext in (".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".webp"):
        text = _ocr_image_file(filepath)
        return text, "ocr_image"

    # PDF: try text-layer methods first (fast, no API needed)
    for fn, name in [
        (_extract_pdfplumber, "pdfplumber"),
        (_extract_pymupdf,    "pymupdf"),
        (_extract_pdfminer,   "pdfminer"),
    ]:
        t = fn(filepath)
        if _alphanum_len(t) >= TEXT_MIN_CHARS:
            print(f"[Extractor] {name} extracted {_alphanum_len(t)} chars")
            return t, name

    # All text methods failed → scanned PDF, render pages and OCR
    print("[Extractor] Text layer empty — rendering pages for OCR...")
    t = _ocr_pdf(filepath)
    if _alphanum_len(t) >= TEXT_MIN_CHARS:
        print(f"[Extractor] OCR extracted {_alphanum_len(t)} chars")
        return t, "ocr_pdf"

    return "", "failed"


# ══════════════════════════════════════════════════════════════════════════════
# LLM EXTRACTION
# ══════════════════════════════════════════════════════════════════════════════

def _call_text_llm(text: str) -> dict:
    """Send extracted text to LLM. Fast and cheap — preferred path."""
    llm = get_llm(vision=False)
    prompt = f"{EXTRACTION_PROMPT}\n\nDocument text:\n{text[:8000]}"
    response = llm.invoke(prompt)
    raw = response.content if hasattr(response, "content") else str(response)
    return _extract_json_from_response(raw)


@retry(stop=stop_after_attempt(VISION_LLM_RETRIES), wait=wait_exponential(multiplier=1, min=2, max=6))
def _call_vision_llm(doc_path: str) -> dict:
    """Vision LLM — last resort when text extraction completely fails."""
    print("[Extractor] Falling back to Vision LLM...")
    llm = get_llm(vision=True)
    messages = build_vision_message(doc_path, EXTRACTION_PROMPT)
    response = llm.invoke(messages)
    raw = response.content if hasattr(response, "content") else str(response)
    return _extract_json_from_response(raw)


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _extract_json_from_response(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"```json\s*", "", text)
    text = re.sub(r"```\s*", "", text)
    return json.loads(text.strip())


def _validate_extraction_structure(data: dict, method: str = "text") -> dict:
    result = {}
    for field in REQUIRED_FIELDS:
        if field in data and isinstance(data[field], dict):
            val  = data[field].get("value")
            conf = float(data[field].get("confidence", 0.0))
            conf = max(0.0, min(1.0, conf))
            if val is None or str(val).strip() in ("", "null"):
                val  = None
                conf = min(conf, 0.3)
            result[field] = {"value": val, "confidence": conf, "method": method}
        else:
            result[field] = {"value": None, "confidence": 0.0, "method": method}
    return result


def _average_confidence(extraction: dict) -> float:
    confs = [v["confidence"] for v in extraction.values() if isinstance(v, dict)]
    return sum(confs) / len(confs) if confs else 0.0


# ══════════════════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

def run_extractor(doc_path: str) -> dict:
    """
    Main extraction entry point.
    
    Step 1: Extract text (pdfplumber → PyMuPDF → pdfminer → OCR)
    Step 2: Send text to LLM  [fast path — no vision needed]
    Step 3: Vision LLM only if text extraction fully failed
    
    Returns: {field_name: {value, confidence, method}}
    """
    if not os.path.exists(doc_path):
        raise FileNotFoundError(f"Document not found: {doc_path}")

    # Step 1: Extract text
    text, extraction_method = extract_text(doc_path)

    # Step 2: Send text to LLM (fast, cheap)
    if text and _alphanum_len(text) >= TEXT_MIN_CHARS:
        try:
            raw = _call_text_llm(text)
            return _validate_extraction_structure(raw, method=extraction_method)
        except Exception as e:
            print(f"[Extractor] Text LLM failed: {e}")
            # fall through to vision

    # Step 3: Vision LLM as last resort
    print(f"[Extractor] Text extraction yielded {_alphanum_len(text)} chars — trying Vision LLM")
    try:
        raw = _call_vision_llm(doc_path)
        return _validate_extraction_structure(raw, method="vision_llm")
    except Exception as e:
        print(f"[Extractor] Vision LLM also failed: {e}")
        return _validate_extraction_structure({}, method="failed")


def format_extraction_for_display(extraction: dict) -> list[dict]:
    rows = []
    for field, data in extraction.items():
        conf   = data.get("confidence", 0.0)
        status = "high" if conf >= 0.85 else "medium" if conf >= CONFIDENCE_LOW else "low"
        rows.append({
            "field":              field.replace("_", " ").title(),
            "field_key":          field,
            "value":              data.get("value") or "NOT FOUND",
            "confidence":         conf,
            "confidence_pct":     f"{conf*100:.0f}%",
            "confidence_status":  status,
            "method":             data.get("method", "unknown"),
        })
    return rows


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        result = run_extractor(sys.argv[1])
        print(json.dumps(result, indent=2))
    else:
        print("Usage: python -m agents.extractor <doc_path>")