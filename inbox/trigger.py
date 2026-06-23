"""
Part 2 — Inbox Trigger
Watches ./inbox/incoming/ for new email JSON files dropped by SU.
Each email JSON contains:
  - from: sender
  - subject: email subject
  - body: email body text
  - customer_id: which customer's rules to validate against
  - attachments: list of file paths (PDFs/images)

When a new file appears, it fires the full pipeline and stores the result.
This simulates the "email arrives → agent wakes up" trigger.
"""

import os
import sys
import json
import time
import shutil
import traceback
from pathlib import Path
from datetime import datetime

# Make sure imports work from repo root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.graph import run_pipeline
from db.database import init_db

WATCH_DIR   = Path(__file__).parent / "incoming"
DONE_DIR    = Path(__file__).parent / "processed"
FAILED_DIR  = Path(__file__).parent / "failed"
RESULTS_DIR = Path(__file__).parent / "results"

for d in (WATCH_DIR, DONE_DIR, FAILED_DIR, RESULTS_DIR):
    d.mkdir(parents=True, exist_ok=True)


def process_email(email_path: Path) -> dict:
    """Process one email JSON file → run pipeline → store result."""
    with open(email_path) as f:
        email = json.load(f)

    print(f"\n[Trigger] New email from '{email.get('from', '?')}': {email.get('subject', '?')}")

    attachments = email.get("attachments", [])
    if not attachments:
        raise ValueError("Email has no attachments — nothing to validate.")

    # Resolve attachment paths relative to the email file's directory
    email_dir = email_path.parent
    docs = []
    for att in attachments:
        att_path = Path(att)
        if not att_path.is_absolute():
            att_path = email_dir / att_path
        if not att_path.exists():
            raise FileNotFoundError(f"Attachment not found: {att_path}")
        docs.append((str(att_path), att_path.name))

    customer_id = email.get("customer_id", "CUST001")
    print(f"[Trigger] Running pipeline: {len(docs)} doc(s) for customer {customer_id}")

    result = run_pipeline(docs, customer_id)

    # Attach email metadata to result
    result["email_from"]    = email.get("from", "unknown@supplier.com")
    result["email_subject"] = email.get("subject", "(no subject)")
    result["email_body"]    = email.get("body", "")
    result["received_at"]   = datetime.utcnow().isoformat()
    result["source_email"]  = email_path.name

    # Save result
    result_file = RESULTS_DIR / f"{result['shipment_id']}.json"
    with open(result_file, "w") as f:
        json.dump(result, f, indent=2, default=str)

    print(f"[Trigger] ✅ Shipment {result['shipment_id']} → {result['decision'].upper()}")
    return result


def watch_inbox(poll_interval: float = 2.0):
    """Poll the incoming folder and process any new .json email files."""
    init_db()
    print(f"[Trigger] Watching {WATCH_DIR} for incoming SU emails...")
    print(f"[Trigger] Drop a .json email file into {WATCH_DIR} to trigger the pipeline.\n")

    seen = set()

    while True:
        for email_file in sorted(WATCH_DIR.glob("*.json")):
            if email_file.name in seen:
                continue
            seen.add(email_file.name)

            try:
                result = process_email(email_file)
                shutil.move(str(email_file), str(DONE_DIR / email_file.name))
                print(f"[Trigger] Moved to processed/: {email_file.name}")
            except Exception as e:
                print(f"[Trigger] ❌ Failed to process {email_file.name}: {e}")
                traceback.print_exc()
                shutil.move(str(email_file), str(FAILED_DIR / email_file.name))

        time.sleep(poll_interval)


def process_single(email_path: str) -> dict:
    """Process a single email file (used by the UI for one-shot runs)."""
    init_db()
    return process_email(Path(email_path))


if __name__ == "__main__":
    if len(sys.argv) > 1:
        # One-shot: python -m inbox.trigger path/to/email.json
        result = process_single(sys.argv[1])
        print(json.dumps(result, indent=2, default=str))
    else:
        watch_inbox()
