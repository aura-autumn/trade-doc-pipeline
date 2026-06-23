"""
Router / Decision Agent
- Reads validator output + summary
- Decides: auto_approve | flag_for_review | draft_amendment
- Generates reasoning and draft amendment email
- NEVER auto-sends. Human always reviews.
"""

import os
import json
from dotenv import load_dotenv

from llm.client import get_llm
from agents.validator import get_validation_summary
from core.logging_config import get_logger

load_dotenv()

log = get_logger(__name__)


def _decide_routing(summary: dict) -> str:
    """Rule-based routing decision. LLM only used for reasoning and email draft."""
    has_critical      = summary["has_critical_issues"]
    mismatches        = summary["mismatches"]
    missing           = summary["missing"]
    uncertain         = summary["uncertain"]
    critical_uncertain = summary["critical_uncertain_fields"]

    if mismatches == 0 and missing == 0 and len(critical_uncertain) == 0 and uncertain == 0:
        return "auto_approve"

    if has_critical or mismatches >= 2 or missing >= 2:
        return "draft_amendment"

    return "flag_for_review"


def _build_router_prompt(
    validation_results: list[dict],
    summary: dict,
    decision: str,
    shipment_id: str,
    customer_name: str,
    extraction: dict,
) -> str:
    # Build a full field-by-field breakdown — every field, every status
    all_rows = []
    for r in validation_results:
        status = r["status"].upper()
        field  = r["field_name"].replace("_", " ").title()
        found  = r.get("found_value") or "NOT FOUND"
        expected = r.get("expected_value") or "any"
        critical = "⚠ CRITICAL" if r.get("is_critical") else ""
        detail   = r.get("detail", "")
        conf     = r.get("confidence", 0.0)
        all_rows.append(
            f"  • {field}: {status} {critical} | "
            f"Found: '{found}' | Expected: '{expected}' | "
            f"Confidence: {conf:.0%}"
            + (f" | Note: {detail}" if detail else "")
        )
    fields_breakdown = "\n".join(all_rows)

    # Pull key doc identifiers from extraction for email context
    invoice_no    = extraction.get("invoice_number", {}).get("value") or "N/A"
    consignee     = extraction.get("consignee_name", {}).get("value") or customer_name or "N/A"
    port_loading  = extraction.get("port_of_loading", {}).get("value") or "N/A"
    port_discharge = extraction.get("port_of_discharge", {}).get("value") or "N/A"

    issues_only = [r for r in validation_results if r["status"] not in ("match", "not_checked")]
    not_checked = [r for r in validation_results if r["status"] == "not_checked"]

    if decision == "draft_amendment":
        email_instruction = (
            "Write a professional amendment request email to the supplier. "
            "Subject line must reference the invoice number and customer name. "
            "Body must list EACH discrepancy with: field name, what was found, what was expected. "
            "End with a clear request for corrected documents and a deadline reminder."
        )
    elif decision == "flag_for_review":
        email_instruction = (
            "Write a brief internal review note for the human validator. "
            "Subject line should say 'Review Required'. "
            "List which fields need manual attention and why."
        )
    else:
        email_instruction = (
            "Write a concise approval confirmation note. "
            f"Subject: Trade Document Approved — Invoice {invoice_no}. "
            f"Mention customer name ({consignee}), invoice number, and that all validated fields passed. "
            "If any fields were not checked (no rule defined), list them by name so the reviewer is aware."
        )

    not_checked_note = ""
    if not_checked:
        names = ", ".join(r["field_name"].replace("_", " ").title() for r in not_checked)
        not_checked_note = (
            f"\nNote: {len(not_checked)} field(s) had no customer rule defined and were not validated: {names}. "
            "Mention these in your reasoning so the reviewer knows they exist."
        )

    return f"""You are a trade document validation assistant for a cargo management team.

Shipment context:
- Shipment ID: {shipment_id or "N/A"}
- Customer: {customer_name or "N/A"}
- Consignee: {consignee}
- Invoice number: {invoice_no}
- Port of loading: {port_loading}
- Port of discharge: {port_discharge}

Validation results — ALL fields:
{fields_breakdown}

Summary:
- Total fields: {summary['total_fields']} | Matches: {summary['matches']} | Mismatches: {summary['mismatches']}
- Missing: {summary['missing']} | Uncertain: {summary['uncertain']} | Not checked: {summary['not_checked']}
- Decision: {decision.upper()}
{not_checked_note}

Your tasks:
1. REASONING: 2-3 sentences. Name specific fields that caused the decision. 
   Explain what matched, what didn't, and what was skipped (not_checked).
   Do NOT say "6 out of 8" without naming the other 2.

2. EMAIL/NOTE: {email_instruction}

Respond ONLY in this exact JSON format:
{{
  "reasoning": "...",
  "draft_email": "Subject: ...\\n\\n..."
}}

No markdown. No extra text. Only JSON."""


def run_router(
    validation_results: list[dict],
    shipment_id: str = "",
    customer_name: str = "",
    extraction: dict = None,
) -> dict:
    """
    Main router function.
    Returns: {decision, reasoning, draft_email, summary}
    """
    if extraction is None:
        extraction = {}

    summary  = get_validation_summary(validation_results)
    decision = _decide_routing(summary)

    try:
        llm    = get_llm(vision=False)
        prompt = _build_router_prompt(
            validation_results, summary, decision,
            shipment_id, customer_name, extraction
        )
        response = llm.invoke(prompt)
        raw = response.content if hasattr(response, "content") else str(response)

        raw = raw.strip()
        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        llm_output = json.loads(raw)
        reasoning   = llm_output.get("reasoning", "")
        draft_email = llm_output.get("draft_email", "")

    except Exception as e:
        log.warning("Router LLM call failed: %s. Using deterministic fallback reasoning/email.", e)
        reasoning   = _fallback_reasoning(decision, summary)
        draft_email = _fallback_email(decision, validation_results, summary, customer_name, extraction)

    return {
        "decision":    decision,
        "reasoning":   reasoning,
        "draft_email": draft_email,
        "summary":     summary,
    }


def _fallback_reasoning(decision: str, summary: dict) -> str:
    if decision == "auto_approve":
        checked = summary["matches"]
        not_checked = summary["not_checked"]
        nc_note = f" {not_checked} field(s) had no customer rule and were not validated." if not_checked else ""
        return f"All {checked} validated fields matched customer rules with no mismatches or uncertain values.{nc_note}"
    elif decision == "draft_amendment":
        issues = []
        if summary["mismatches"]:
            fields = ", ".join(summary["critical_mismatch_fields"]) or "multiple fields"
            issues.append(f"{summary['mismatches']} mismatch(es) including: {fields}")
        if summary["missing"]:
            issues.append(f"{summary['missing']} required field(s) missing")
        return f"Amendment required: {'; '.join(issues)}."
    else:
        return (
            f"Flagged for review: {summary['uncertain']} uncertain field(s) and "
            f"{summary['mismatches']} mismatch(es) need manual verification."
        )


def _fallback_email(
    decision: str,
    validation_results: list,
    summary: dict,
    customer_name: str,
    extraction: dict,
) -> str:
    invoice_no = extraction.get("invoice_number", {}).get("value") or "N/A"
    consignee  = extraction.get("consignee_name", {}).get("value") or customer_name or "N/A"

    if decision == "auto_approve":
        not_checked = [r for r in validation_results if r["status"] == "not_checked"]
        nc_lines = ""
        if not_checked:
            names = ", ".join(r["field_name"].replace("_", " ").title() for r in not_checked)
            nc_lines = f"\nNote: The following fields had no validation rule and were not checked: {names}.\n"
        return (
            f"Subject: Trade Document Approved — Invoice {invoice_no}\n\n"
            f"Dear Team,\n\n"
            f"All validated fields for {consignee} (Invoice: {invoice_no}) have passed customer rule checks.\n"
            f"{nc_lines}\n"
            f"This shipment is approved for processing.\n\n"
            f"Regards,\nNova Validation System"
        )

    issues = [r for r in validation_results if r["status"] not in ("match", "not_checked")]
    lines = [
        f"Subject: Amendment Required — Invoice {invoice_no} ({consignee})",
        "",
        "Dear Supplier,",
        "",
        f"We have reviewed the trade documents for Invoice {invoice_no} and found the following discrepancies:",
        "",
    ]
    for r in issues:
        lines.append(
            f"• {r['field_name'].replace('_', ' ').title()}: "
            f"Found '{r.get('found_value', 'MISSING')}' — "
            f"Expected '{r.get('expected_value', 'per customer requirements')}'"
            + (" [CRITICAL]" if r.get("is_critical") else "")
        )
    lines += [
        "",
        "Please submit corrected documents at your earliest convenience.",
        "",
        "Regards,",
        "Nova Validation Team",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    mock_validation = [
        {"field_name": "incoterms", "status": "mismatch", "found_value": "FOB", "expected_value": "CIF", "is_critical": True, "confidence": 0.97, "detail": "Expected exactly 'CIF', found 'FOB'"},
        {"field_name": "consignee_name", "status": "match", "found_value": "ACME IMPORTS LTD", "expected_value": "ACME IMPORTS LTD", "is_critical": True, "confidence": 0.95, "detail": ""},
        {"field_name": "gross_weight", "status": "uncertain", "found_value": "1200 KGS", "expected_value": None, "is_critical": False, "confidence": 0.45, "detail": "Low confidence"},
        {"field_name": "invoice_number", "status": "not_checked", "found_value": "INV-001", "expected_value": None, "is_critical": False, "confidence": 0.98, "detail": "No rule defined"},
    ]
    mock_extraction = {
        "invoice_number": {"value": "INV-001", "confidence": 0.98},
        "consignee_name": {"value": "ACME IMPORTS LTD", "confidence": 0.95},
    }
    result = run_router(mock_validation, customer_name="Acme Imports", extraction=mock_extraction)
    print(json.dumps(result, indent=2, default=str))