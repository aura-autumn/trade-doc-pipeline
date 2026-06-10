"""
Eval Script
- Runs pipeline on labeled test documents
- Compares extracted fields to ground truth
- Reports: field accuracy, confidence calibration, flag rate, cost estimate
"""

import os
import json
import time
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


GROUND_TRUTH_PATH = "./data/ground_truth.json"


def load_ground_truth() -> list[dict]:
    """Load ground truth labels for eval documents."""
    if not os.path.exists(GROUND_TRUTH_PATH):
        print(f"Ground truth file not found: {GROUND_TRUTH_PATH}")
        print("Creating sample ground truth file...")
        create_sample_ground_truth()
    with open(GROUND_TRUTH_PATH) as f:
        return json.load(f)


def create_sample_ground_truth():
    """Create a sample ground truth file for testing."""
    sample = [
        {
            "doc_path": "./data/sample_docs/sample_clean.pdf",
            "customer_id": "CUST001",
            "expected_extraction": {
                "consignee_name": "ACME IMPORTS LTD",
                "hs_code": "84713000",
                "port_of_loading": "SHANGHAI",
                "port_of_discharge": "NHAVA SHEVA",
                "incoterms": "CIF",
                "description_of_goods": "Laptop Computers and Accessories",
                "gross_weight": "1250 KGS",
                "invoice_number": "INV-2024-001",
            },
            "expected_decision": "auto_approve",
            "doc_type": "clean",
        },
        {
            "doc_path": "./data/sample_docs/sample_mismatch.pdf",
            "customer_id": "CUST001",
            "expected_extraction": {
                "consignee_name": "ACME IMPORTS LTD",
                "hs_code": "84713000",
                "port_of_loading": "SHANGHAI",
                "port_of_discharge": "NHAVA SHEVA",
                "incoterms": "FOB",  # mismatch - should be CIF
                "description_of_goods": "Laptop Computers",
                "gross_weight": "1100 KGS",
                "invoice_number": "INV-2024-002",
            },
            "expected_decision": "draft_amendment",
            "doc_type": "mismatch",
        },
    ]
    os.makedirs(os.path.dirname(GROUND_TRUTH_PATH), exist_ok=True)
    with open(GROUND_TRUTH_PATH, "w") as f:
        json.dump(sample, f, indent=2)
    print(f"Sample ground truth created: {GROUND_TRUTH_PATH}")


def evaluate_extraction(extracted: dict, expected: dict) -> dict:
    """
    Compare extracted fields to ground truth.
    Returns per-field accuracy metrics.
    """
    results = {}
    total = len(expected)
    correct = 0
    confident_correct = 0
    confident_wrong = 0

    for field, expected_val in expected.items():
        ext = extracted.get(field, {})
        found_val = ext.get("value", "")
        confidence = ext.get("confidence", 0.0)

        # Normalize for comparison
        found_norm = str(found_val).upper().strip() if found_val else ""
        expected_norm = str(expected_val).upper().strip() if expected_val else ""

        is_correct = found_norm == expected_norm or expected_norm in found_norm

        if is_correct:
            correct += 1
            if confidence >= 0.85:
                confident_correct += 1
        else:
            if confidence >= 0.85:
                confident_wrong += 1  # Bad: high confidence but wrong

        results[field] = {
            "expected": expected_val,
            "found": found_val,
            "confidence": confidence,
            "correct": is_correct,
            "calibration_issue": confidence >= 0.85 and not is_correct,
        }

    return {
        "per_field": results,
        "total_fields": total,
        "correct": correct,
        "accuracy": correct / total if total > 0 else 0,
        "confident_correct": confident_correct,
        "confident_wrong": confident_wrong,
        "calibration_score": 1 - (confident_wrong / total) if total > 0 else 1.0,
    }


def evaluate_decision(actual_decision: str, expected_decision: str) -> dict:
    return {
        "expected": expected_decision,
        "actual": actual_decision,
        "correct": actual_decision == expected_decision,
    }


def run_eval(test_cases: list[dict] = None) -> dict:
    """
    Run full evaluation pipeline on test cases.
    """
    from db.database import init_db, seed_demo_customers
    from pipeline.graph import run_pipeline

    init_db()
    seed_demo_customers()

    if test_cases is None:
        test_cases = load_ground_truth()

    # Filter to existing docs only
    test_cases = [tc for tc in test_cases if os.path.exists(tc["doc_path"])]

    if not test_cases:
        print("No test documents found. Add documents to ./data/sample_docs/")
        return {}

    all_results = []
    total_time = 0

    for i, tc in enumerate(test_cases):
        print(f"\n[Eval {i+1}/{len(test_cases)}] {tc['doc_path']} | Customer: {tc['customer_id']}")

        start = time.time()
        try:
            state = run_pipeline(tc["doc_path"], tc["customer_id"])
            elapsed = time.time() - start

            extraction_eval = evaluate_extraction(
                state.get("extraction", {}),
                tc.get("expected_extraction", {})
            )
            decision_eval = evaluate_decision(
                state.get("decision", ""),
                tc.get("expected_decision", "")
            )

            result = {
                "doc": tc["doc_path"],
                "doc_type": tc.get("doc_type", "unknown"),
                "customer_id": tc["customer_id"],
                "elapsed_seconds": round(elapsed, 2),
                "extraction": extraction_eval,
                "decision": decision_eval,
                "error": state.get("error", ""),
            }
            all_results.append(result)
            total_time += elapsed

            print(f"  Extraction accuracy: {extraction_eval['accuracy']:.0%}")
            print(f"  Decision: {decision_eval['actual']} (expected: {decision_eval['expected']}) — {'✓' if decision_eval['correct'] else '✗'}")
            print(f"  Time: {elapsed:.1f}s")

        except Exception as e:
            print(f"  FAILED: {e}")
            all_results.append({
                "doc": tc["doc_path"],
                "error": str(e),
                "extraction": {},
                "decision": {"correct": False},
            })

    # Aggregate metrics
    valid = [r for r in all_results if not r.get("error")]
    if not valid:
        print("\nNo successful eval runs.")
        return {"results": all_results}

    avg_accuracy = sum(r["extraction"]["accuracy"] for r in valid) / len(valid)
    decision_accuracy = sum(1 for r in valid if r["decision"]["correct"]) / len(valid)
    avg_time = total_time / len(test_cases)

    # Field-level accuracy breakdown
    field_stats = {}
    for r in valid:
        for field, fdata in r["extraction"].get("per_field", {}).items():
            if field not in field_stats:
                field_stats[field] = {"correct": 0, "total": 0, "calibration_issues": 0}
            field_stats[field]["total"] += 1
            if fdata["correct"]:
                field_stats[field]["correct"] += 1
            if fdata.get("calibration_issue"):
                field_stats[field]["calibration_issues"] += 1

    report = {
        "summary": {
            "total_docs": len(test_cases),
            "successful_runs": len(valid),
            "avg_extraction_accuracy": round(avg_accuracy, 3),
            "decision_accuracy": round(decision_accuracy, 3),
            "avg_latency_seconds": round(avg_time, 2),
        },
        "field_accuracy": {
            field: {
                "accuracy": round(s["correct"] / s["total"], 3) if s["total"] else 0,
                "calibration_issues": s["calibration_issues"],
            }
            for field, s in field_stats.items()
        },
        "results": all_results,
    }

    print("\n" + "="*50)
    print("EVAL SUMMARY")
    print("="*50)
    print(f"Docs evaluated:          {report['summary']['total_docs']}")
    print(f"Extraction accuracy:     {avg_accuracy:.0%}")
    print(f"Decision accuracy:       {decision_accuracy:.0%}")
    print(f"Avg latency:             {avg_time:.1f}s")
    print("\nPer-field accuracy:")
    for field, stats in report["field_accuracy"].items():
        calib = f" ⚠ {stats['calibration_issues']} calibration issue(s)" if stats["calibration_issues"] else ""
        print(f"  {field:<30} {stats['accuracy']:.0%}{calib}")

    # Save report
    report_path = "./data/eval_report.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\nFull report saved: {report_path}")

    return report


if __name__ == "__main__":
    run_eval()
