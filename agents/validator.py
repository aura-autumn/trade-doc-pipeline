"""
Validator Agent
- Takes extracted fields + customer rule set
- Validates each field: match | mismatch | uncertain | missing
- NEVER silently approves uncertain fields
- Returns field-by-field result with found vs expected
"""

import os
import re
from dotenv import load_dotenv

from db.database import get_customer_rules

load_dotenv()

CONFIDENCE_LOW = float(os.getenv("CONFIDENCE_THRESHOLD_LOW", 0.6))


def _apply_rule(found_value: str, rule: dict) -> tuple[str, str]:
    """
    Apply a single rule to a found value.
    Returns (status, detail_message)
    status: match | mismatch
    """
    rule_type = rule["rule_type"]
    expected = rule.get("expected_value", "")

    if rule_type == "not_null":
        if found_value and found_value.strip():
            return "match", ""
        else:
            return "mismatch", "Field is required but missing"

    if not found_value or not found_value.strip():
        return "mismatch", f"Field is empty, expected: {expected}"

    found_upper = found_value.upper().strip()

    if rule_type == "exact":
        expected_upper = expected.upper().strip()
        if found_upper == expected_upper:
            return "match", ""
        else:
            return "mismatch", f"Expected exactly '{expected}', found '{found_value}'"

    elif rule_type == "contains":
        expected_upper = expected.upper().strip()
        if expected_upper in found_upper:
            return "match", ""
        else:
            return "mismatch", f"Expected to contain '{expected}', found '{found_value}'"

    elif rule_type == "regex":
        try:
            pattern = re.compile(expected, re.IGNORECASE)
            if pattern.search(found_value):
                return "match", ""
            else:
                return "mismatch", f"Expected pattern '{expected}', found '{found_value}'"
        except re.error:
            return "mismatch", f"Invalid rule pattern '{expected}'"

    return "mismatch", f"Unknown rule type: {rule_type}"


def run_validator(extraction: dict, customer_id: str) -> list[dict]:
    """
    Main validator function.

    Args:
        extraction: {field_name: {value, confidence, method}}
        customer_id: str

    Returns:
        list of {
            field_name, status, found_value, expected_value,
            rule_type, is_critical, confidence, detail
        }
    """
    rules = get_customer_rules(customer_id)
    rules_by_field = {r["field_name"]: r for r in rules}

    results = []

    # Validate all extracted fields against rules
    all_fields = set(extraction.keys()) | set(rules_by_field.keys())

    for field_name in all_fields:
        extracted = extraction.get(field_name, {})
        found_value = extracted.get("value")
        confidence = extracted.get("confidence", 0.0)
        rule = rules_by_field.get(field_name)

        # Field has no rule — just surface it, mark not_checked
        if rule is None:
            results.append({
                "field_name": field_name,
                "status": "not_checked",
                "found_value": found_value,
                "expected_value": None,
                "rule_type": None,
                "is_critical": False,
                "confidence": confidence,
                "detail": "No rule defined for this field",
            })
            continue

        # Low confidence = uncertain regardless of value match
        # NEVER silently approve uncertain fields
        if confidence < CONFIDENCE_LOW:
            results.append({
                "field_name": field_name,
                "status": "uncertain",
                "found_value": found_value,
                "expected_value": rule.get("expected_value"),
                "rule_type": rule["rule_type"],
                "is_critical": bool(rule.get("is_critical")),
                "confidence": confidence,
                "detail": f"Low confidence ({confidence:.0%}). Manual review required.",
            })
            continue

        # Field missing entirely
        if found_value is None and rule["rule_type"] != "not_null":
            results.append({
                "field_name": field_name,
                "status": "missing",
                "found_value": None,
                "expected_value": rule.get("expected_value"),
                "rule_type": rule["rule_type"],
                "is_critical": bool(rule.get("is_critical")),
                "confidence": 0.0,
                "detail": "Field not found in document",
            })
            continue

        # Apply rule
        status, detail = _apply_rule(str(found_value) if found_value else "", rule)
        results.append({
            "field_name": field_name,
            "status": status,
            "found_value": found_value,
            "expected_value": rule.get("expected_value"),
            "rule_type": rule["rule_type"],
            "is_critical": bool(rule.get("is_critical")),
            "confidence": confidence,
            "detail": detail,
        })

    return results


def get_validation_summary(validation_results: list[dict]) -> dict:
    """Summarize validation results for router decision-making."""
    total = len(validation_results)
    matches = sum(1 for r in validation_results if r["status"] == "match")
    mismatches = sum(1 for r in validation_results if r["status"] == "mismatch")
    uncertain = sum(1 for r in validation_results if r["status"] == "uncertain")
    missing = sum(1 for r in validation_results if r["status"] == "missing")
    not_checked = sum(1 for r in validation_results if r["status"] == "not_checked")

    critical_mismatches = [
        r for r in validation_results
        if r["status"] in ("mismatch", "missing") and r.get("is_critical")
    ]
    critical_uncertain = [
        r for r in validation_results
        if r["status"] == "uncertain" and r.get("is_critical")
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


if __name__ == "__main__":
    import json
    # Quick test
    mock_extraction = {
        "consignee_name": {"value": "ACME IMPORTS LTD", "confidence": 0.95},
        "hs_code": {"value": "84713000", "confidence": 0.90},
        "port_of_loading": {"value": "SHANGHAI", "confidence": 0.88},
        "port_of_discharge": {"value": "NHAVA SHEVA", "confidence": 0.92},
        "incoterms": {"value": "FOB", "confidence": 0.97},  # mismatch - should be CIF
        "description_of_goods": {"value": "Laptop computers", "confidence": 0.91},
        "gross_weight": {"value": "1200 KGS", "confidence": 0.45},  # low confidence = uncertain
        "invoice_number": {"value": "INV-2024-001", "confidence": 0.98},
    }
    results = run_validator(mock_extraction, "CUST001")
    print(json.dumps(results, indent=2))
    print("\nSummary:")
    print(json.dumps(get_validation_summary(results), indent=2))
