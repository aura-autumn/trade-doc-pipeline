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

load_dotenv()


ROUTING_RULES = """
Decision logic:
1. auto_approve: ALL of the following must be true:
   - Zero mismatches
   - Zero missing fields
   - Zero uncertain fields on critical rules
   - All confidence scores >= 0.6

2. draft_amendment: ANY of the following:
   - One or more critical mismatches
   - One or more critical fields missing
   - Multiple non-critical mismatches (2+)

3. flag_for_review: Everything else:
   - Uncertain fields on critical rules
   - Single non-critical mismatch
   - Unusual combination the router can't resolve cleanly
"""


def _decide_routing(summary: dict) -> str:
    """Rule-based routing decision. LLM only used for reasoning and email draft."""
    has_critical = summary["has_critical_issues"]
    mismatches = summary["mismatches"]
    missing = summary["missing"]
    uncertain = summary["uncertain"]
    critical_uncertain = summary["critical_uncertain_fields"]

    # Auto approve: clean pass
    if mismatches == 0 and missing == 0 and len(critical_uncertain) == 0 and uncertain == 0:
        return "auto_approve"

    # Draft amendment: critical issues or multiple mismatches
    if has_critical or mismatches >= 2 or missing >= 2:
        return "draft_amendment"

    # Flag for review: edge cases, uncertain criticals, single minor issues
    return "flag_for_review"


def _build_router_prompt(validation_results: list[dict], summary: dict, decision: str) -> str:
    issues = summary.get("issues", [])
    issues_text = "\n".join([
        f"- {r['field_name']}: {r['status'].upper()} | "
        f"Found: '{r.get('found_value', 'N/A')}' | "
        f"Expected: '{r.get('expected_value', 'N/A')}' | "
        f"Critical: {r.get('is_critical', False)} | "
        f"Detail: {r.get('detail', '')}"
        for r in issues
    ]) or "No issues found."

    return f"""You are a trade document validation assistant for a cargo management team.

Validation summary:
- Total fields checked: {summary['total_fields']}
- Matches: {summary['matches']}
- Mismatches: {summary['mismatches']}
- Missing: {summary['missing']}
- Uncertain (low confidence): {summary['uncertain']}
- Decision made: {decision}

Issues found:
{issues_text}

Your tasks:
1. Write a clear, concise REASONING for the decision in 2-3 sentences. Be specific about which fields caused the decision.

2. {"Write a professional amendment request EMAIL to the supplier listing every discrepancy. Include: field name, what was found, what was expected. Be specific. End with a clear request for corrected documents." if decision == "draft_amendment" else "Write a brief NOTE for the human reviewer summarizing what needs their attention." if decision == "flag_for_review" else "Write a brief APPROVAL NOTE confirming all fields validated successfully."}

Respond ONLY in this JSON format:
{{
  "reasoning": "...",
  "draft_email": "Subject: ...\\n\\n..."
}}

No markdown. No extra text. Only JSON.
"""


def run_router(validation_results: list[dict], shipment_id: str = "", customer_name: str = "") -> dict:
    """
    Main router function.

    Returns: {
        decision: str,
        reasoning: str,
        draft_email: str
    }
    """
    summary = get_validation_summary(validation_results)
    decision = _decide_routing(summary)

    # Use LLM for reasoning and email drafting
    try:
        llm = get_llm(vision=False)
        prompt = _build_router_prompt(validation_results, summary, decision)
        response = llm.invoke(prompt)
        raw = response.content if hasattr(response, "content") else str(response)

        # Clean and parse
        raw = raw.strip().lstrip("```json").rstrip("```").strip()
        llm_output = json.loads(raw)
        reasoning = llm_output.get("reasoning", "")
        draft_email = llm_output.get("draft_email", "")

    except Exception as e:
        print(f"Router LLM call failed: {e}. Using fallback reasoning.")
        reasoning = _fallback_reasoning(decision, summary)
        draft_email = _fallback_email(decision, validation_results, summary, customer_name)

    return {
        "decision": decision,
        "reasoning": reasoning,
        "draft_email": draft_email,
        "summary": summary,
    }


def _fallback_reasoning(decision: str, summary: dict) -> str:
    """Rule-based fallback reasoning if LLM fails."""
    if decision == "auto_approve":
        return f"All {summary['matches']} fields validated successfully with no issues."
    elif decision == "draft_amendment":
        issues = []
        if summary["mismatches"]:
            issues.append(f"{summary['mismatches']} field(s) mismatched")
        if summary["missing"]:
            issues.append(f"{summary['missing']} required field(s) missing")
        if summary["critical_mismatch_fields"]:
            issues.append(f"Critical fields affected: {', '.join(summary['critical_mismatch_fields'])}")
        return f"Amendment required: {'; '.join(issues)}."
    else:
        return (
            f"Flagged for human review: {summary['uncertain']} field(s) have low confidence "
            f"and {summary['mismatches']} mismatch(es) need verification."
        )


def _fallback_email(decision: str, validation_results: list, summary: dict, customer_name: str) -> str:
    """Rule-based fallback email if LLM fails."""
    if decision == "auto_approve":
        return "Subject: Document Approved\n\nAll fields have been validated successfully. Documents approved."

    issues = [r for r in validation_results if r["status"] not in ("match", "not_checked")]

    lines = [
        f"Subject: Amendment Required — Trade Document Discrepancies",
        "",
        f"Dear Supplier,",
        "",
        "We have reviewed the submitted trade documents and identified the following discrepancies that require correction:",
        "",
    ]

    for r in issues:
        lines.append(
            f"• {r['field_name'].replace('_', ' ').title()}: "
            f"Found '{r.get('found_value', 'MISSING')}' — "
            f"Expected '{r.get('expected_value', 'per customer requirements')}'"
        )

    lines += [
        "",
        "Please submit corrected documents at your earliest convenience.",
        "",
        "Regards,",
        "CG Validation Team",
    ]

    return "\n".join(lines)


if __name__ == "__main__":
    # Quick test
    mock_validation = [
        {"field_name": "incoterms", "status": "mismatch", "found_value": "FOB", "expected_value": "CIF", "is_critical": True, "confidence": 0.97, "detail": "Expected exactly 'CIF', found 'FOB'"},
        {"field_name": "consignee_name", "status": "match", "found_value": "ACME IMPORTS LTD", "expected_value": "ACME IMPORTS LTD", "is_critical": True, "confidence": 0.95, "detail": ""},
        {"field_name": "gross_weight", "status": "uncertain", "found_value": "1200 KGS", "expected_value": None, "is_critical": False, "confidence": 0.45, "detail": "Low confidence"},
        {"field_name": "invoice_number", "status": "match", "found_value": "INV-001", "expected_value": None, "is_critical": True, "confidence": 0.98, "detail": ""},
    ]
    result = run_router(mock_validation, customer_name="Acme Imports")
    print(json.dumps(result, indent=2, default=str))
