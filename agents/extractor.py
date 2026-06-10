"""
Extractor Agent
- Takes a trade document (PDF or image)
- Uses vision LLM as primary
- Falls back to Docling if overall confidence is low
- Outputs structured JSON with per-field confidence scores
"""

import os
import json
import re
from pathlib import Path
from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential

from llm.client import get_llm, build_vision_message

load_dotenv()

CONFIDENCE_LOW = float(os.getenv("CONFIDENCE_THRESHOLD_LOW", 0.6))
DOCLING_FALLBACK_THRESHOLD = float(os.getenv("DOCLING_FALLBACK_THRESHOLD", 0.65))

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


def _extract_json_from_response(text: str) -> dict:
    """Robustly extract JSON from LLM response, handles markdown fences."""
    text = text.strip()
    # Remove markdown fences if present
    text = re.sub(r"```json\s*", "", text)
    text = re.sub(r"```\s*", "", text)
    text = text.strip()
    return json.loads(text)


def _validate_extraction_structure(data: dict) -> dict:
    """
    Ensure all required fields exist with proper structure.
    Missing fields get value=null, confidence=0.0
    """
    result = {}
    for field in REQUIRED_FIELDS:
        if field in data and isinstance(data[field], dict):
            val = data[field].get("value")
            conf = float(data[field].get("confidence", 0.0))
            # Clamp confidence
            conf = max(0.0, min(1.0, conf))
            # Null/empty value = low confidence
            if val is None or str(val).strip() == "" or str(val).lower() == "null":
                val = None
                conf = min(conf, 0.3)
            result[field] = {"value": val, "confidence": conf, "method": "llm"}
        else:
            result[field] = {"value": None, "confidence": 0.0, "method": "llm"}
    return result


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def _call_vision_llm(doc_path: str) -> dict:
    llm = get_llm(vision=True)
    messages = build_vision_message(doc_path, EXTRACTION_PROMPT)
    response = llm.invoke(messages)
    raw = response.content if hasattr(response, "content") else str(response)
    return _extract_json_from_response(raw)


def _extract_with_docling(doc_path: str) -> dict:
    """
    Docling-based extraction fallback for poor quality docs.
    Extracts raw text then uses text LLM.
    """
    try:
        from docling.document_converter import DocumentConverter
        converter = DocumentConverter()
        result = converter.convert(doc_path)
        doc_text = result.document.export_to_markdown()

        llm = get_llm(vision=False)
        prompt = f"""You are a trade document extraction specialist.
Extract trade document fields from the following text.

{EXTRACTION_PROMPT}

Document text:
{doc_text[:8000]}
"""
        response = llm.invoke(prompt)
        raw = response.content if hasattr(response, "content") else str(response)
        data = _extract_json_from_response(raw)
        # Mark as docling method
        for field in data:
            if isinstance(data[field], dict):
                data[field]["method"] = "docling"
        return data
    except Exception as e:
        print(f"Docling extraction failed: {e}")
        return {}


def _average_confidence(extraction: dict) -> float:
    """Calculate average confidence across all fields."""
    confidences = [
        v["confidence"] for v in extraction.values()
        if isinstance(v, dict) and "confidence" in v
    ]
    return sum(confidences) / len(confidences) if confidences else 0.0


def _merge_extractions(primary: dict, fallback: dict) -> dict:
    """
    Merge two extractions. For each field, take the one with higher confidence.
    """
    merged = dict(primary)
    for field, fallback_data in fallback.items():
        if field not in merged:
            merged[field] = fallback_data
        else:
            primary_conf = merged[field].get("confidence", 0.0)
            fallback_conf = fallback_data.get("confidence", 0.0)
            if fallback_conf > primary_conf:
                merged[field] = fallback_data
    return merged


def run_extractor(doc_path: str) -> dict:
    """
    Main extractor function.
    Returns: {field_name: {value, confidence, method}}
    """
    if not os.path.exists(doc_path):
        raise FileNotFoundError(f"Document not found: {doc_path}")

    suffix = Path(doc_path).suffix.lower()
    extraction = {}
    error = None

    # Step 1: Try vision LLM
    try:
        raw_data = _call_vision_llm(doc_path)
        extraction = _validate_extraction_structure(raw_data)
    except Exception as e:
        error = str(e)
        print(f"Vision LLM extraction failed: {e}")
        extraction = _validate_extraction_structure({})

    # Step 2: Check if we need Docling fallback
    avg_conf = _average_confidence(extraction)
    print(f"Extraction average confidence: {avg_conf:.2f}")

    if avg_conf < DOCLING_FALLBACK_THRESHOLD or error:
        print(f"Confidence {avg_conf:.2f} below threshold {DOCLING_FALLBACK_THRESHOLD}. Running Docling fallback...")
        fallback = _extract_with_docling(doc_path)
        if fallback:
            fallback_structured = _validate_extraction_structure(fallback)
            extraction = _merge_extractions(extraction, fallback_structured)
            avg_conf = _average_confidence(extraction)
            print(f"Post-Docling average confidence: {avg_conf:.2f}")

    return extraction


def format_extraction_for_display(extraction: dict) -> list[dict]:
    """Format extraction result as list of dicts for UI display."""
    rows = []
    for field, data in extraction.items():
        conf = data.get("confidence", 0.0)
        status = "high" if conf >= 0.85 else "medium" if conf >= CONFIDENCE_LOW else "low"
        rows.append({
            "field": field.replace("_", " ").title(),
            "field_key": field,
            "value": data.get("value") or "NOT FOUND",
            "confidence": conf,
            "confidence_pct": f"{conf*100:.0f}%",
            "confidence_status": status,
            "method": data.get("method", "llm"),
        })
    return rows


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        doc = sys.argv[1]
        print(f"Extracting: {doc}")
        result = run_extractor(doc)
        print(json.dumps(result, indent=2))
    else:
        print("Usage: python -m agents.extractor <doc_path>")
