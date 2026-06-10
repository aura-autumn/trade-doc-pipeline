"""
Validator Agent
- Takes extracted fields from multiple files inside a customer transaction folder frame
- Validates each document's values against customer rule specifications
- Programmatically reconciles matching metrics across multiple separate files
- Returns structural breakdowns alongside overall transactional evaluation counts
"""

import os
import re
from typing import Any
from dotenv import load_dotenv

load_dotenv()

CONFIDENCE_LOW = float(os.getenv("CONFIDENCE_THRESHOLD_LOW", 0.6))


def _apply_rule(found_value: str, rule: dict) -> tuple[str, str]:
    """Apply a single rule validation check."""
    rule_type = rule["rule_type"]
    expected = rule.get("expected_value", "")

    if rule_type == "not_null":
        if found_value and str(found_value).strip():
            return "match", ""
        else:
            return "mismatch", "Field is required but missing"

    if not found_value or not str(found_value).strip():
        return "mismatch", f"Field is empty, expected: {expected}"

    found_upper = str(found_value).upper().strip()

    if rule_type == "exact":
        expected_upper = str(expected).upper().strip()
        if found_upper == expected_upper:
            return "match", ""
        else:
            return "mismatch", f"Expected exactly '{expected}', found '{found_value}'"

    elif rule_type == "contains":
        expected_upper = str(expected).upper().strip()
        if expected_upper in found_upper:
            return "match", ""
        else:
            return "mismatch", f"Expected text to contain '{expected}', found '{found_value}'"

    elif rule_type == "regex":
        try:
            if re.search(expected, found_value, re.IGNORECASE):
                return "match", ""
            else:
                return "mismatch", f"Value '{found_value}' failed pattern constraint matching: {expected}"
        except Exception as e:
            return "mismatch", f"Regex computation parse issue: {e}"

    return "mismatch", "Unsupported classification check processing execution routine"


def run_validator(extraction_by_doc: dict[str, dict], customer_rules: list[dict]) -> list[dict]:
    """Runs validation mapping logic across single or multiple text structural assets."""
    validation_results = []
    processed_fields_per_doc = {}

    for doc_id, extraction in extraction_by_doc.items():
        processed_fields_per_doc[doc_id] = set()
        
        for rule in customer_rules:
            field = rule["field_name"]
            processed_fields_per_doc[doc_id].add(field)

            if field in extraction:
                val_data = extraction[field]
                val_str = val_data.get("value")
                conf = val_data.get("confidence", 1.0)

                if conf < CONFIDENCE_LOW:
                    validation_results.append({
                        "document_id": doc_id,
                        "field_name": field,
                        "status": "uncertain",
                        "found_value": val_str,
                        "expected_value": rule.get("expected_value"),
                        "rule_type": rule["rule_type"],
                        "is_critical": bool(rule.get("is_critical", 0)),
                        "confidence": conf,
                        "detail": f"Low extraction confidence score threshold record: ({conf:.2f} < {CONFIDENCE_LOW})"
                    })
                    continue

                status, detail = _apply_rule(val_str, rule)
                validation_results.append({
                    "document_id": doc_id,
                    "field_name": field,
                    "status": status,
                    "found_value": val_str,
                    "expected_value": rule.get("expected_value"),
                    "rule_type": rule["rule_type"],
                    "is_critical": bool(rule.get("is_critical", 0)),
                    "confidence": conf,
                    "detail": detail
                })
            else:
                validation_results.append({
                    "document_id": doc_id,
                    "field_name": field,
                    "status": "missing",
                    "found_value": None,
                    "expected_value": rule.get("expected_value"),
                    "rule_type": rule["rule_type"],
                    "is_critical": bool(rule.get("is_critical", 0)),
                    "confidence": 0.0,
                    "detail": "Field could not be extracted or located from file structural logs."
                })

        for field, data in extraction.items():
            if field not in processed_fields_per_doc[doc_id]:
                validation_results.append({
                    "document_id": doc_id,
                    "field_name": field,
                    "status": "not_checked",
                    "found_value": data.get("value"),
                    "expected_value": None,
                    "rule_type": None,
                    "is_critical": False,
                    "confidence": data.get("confidence", 1.0),
                    "detail": "Field passed evaluation tracking logic without active rule checks."
                })

    # Cross-Document Reconciliation Block
    if len(extraction_by_doc) > 1:
        field_cross_maps = {}
        for doc_id, extraction in extraction_by_doc.items():
            for field, data in extraction.items():
                if data.get("value"):
                    field_cross_maps.setdefault(field, {})[doc_id] = str(data["value"]).strip().upper()

        for field, values_dict in field_cross_maps.items():
            unique_values = set(values_dict.values())
            if len(unique_values) > 1:
                is_crit = any(r.get("is_critical", 0) for r in customer_rules if r["field_name"] == field)
                
                validation_results.append({
                    "document_id": None,
                    "field_name": f"cross_doc_discrepancy_{field}",
                    "status": "mismatch",
                    "found_value": f"Conflict: {values_dict}",
                    "expected_value": "Identical match values across all transaction file assets",
                    "rule_type": "cross_document_reconciliation",
                    "is_critical": is_crit or field in ["gross_weight", "invoice_number", "consignee_name"],
                    "confidence": 1.0,
                    "detail": f"Data alignment error: field value varies between uploaded files: {values_dict}"
                })

    return validation_results


def get_validation_summary(validation_results: list[dict]) -> dict:
    total = len(validation_results)
    matches = sum(1 for r in validation_results if r["status"] == "match")
    mismatches = sum(1 for r in validation_results if r["status"] == "mismatch")
    uncertain = sum(1 for r in validation_results if r["status"] == "uncertain")
    missing = sum(1 for r in validation_results if r["status"] == "missing")
    not_checked = sum(1 for r in validation_results if r["status"] == "not_checked")

    critical_mismatches = [
        r for r in validation_results if r["status"] == "mismatch" and r.get("is_critical")
    ]
    critical_uncertain = [
        r for r in validation_results if r["status"] == "uncertain" and r.get("is_critical")
    ]

    return {
        "total_fields": total,
        "matches": matches,
        "mismatches": mismatches,
        "uncertain": uncertain,
        "missing": missing,
        "not_checked": not_checked,
        "has_critical_issues": len(critical_mismatches) > 0 or len(critical_uncertain) > 0,
        "critical_mismatch_fields": [r["field_name"] for r in critical_mismatches],
        "critical_uncertain_fields": [r["field_name"] for r in critical_uncertain],
        "issues": [r for r in validation_results if r["status"] not in ("match", "not_checked")],
    }