"""
Gmail Inbox Trigger — Part 2
Polls a real Gmail inbox for new emails from suppliers (SU).
When a new email with PDF attachments arrives:
  1. Downloads the attachments
  2. Resolves which customer's rules to validate against (via sender domain router)
  3. Fires the full pipeline
  4. Stores result — CG sees it in the verification UI immediately

Setup (one-time):
  1. Go to https://console.cloud.google.com
  2. Create a project → Enable Gmail API
  3. Create OAuth 2.0 credentials (Desktop App) → Download as credentials.json
  4. Place credentials.json in the project root (same folder as this file's parent)
  5. Run this script once — it opens a browser for Gmail OAuth consent
  6. Token is saved to gmail_token.json for all future runs (no re-auth needed)

Config via .env:
  GMAIL_CREDENTIALS_PATH=credentials.json   (default)
  GMAIL_TOKEN_PATH=gmail_token.json         (default)
  GMAIL_LABEL=INBOX                         (watch label, default INBOX)
  GMAIL_POLL_INTERVAL=10                    (seconds between polls, default 10)
  GMAIL_SENDER_FILTER=                      (optional: only process emails from this domain)
  GMAIL_MARK_READ=true                      (mark processed emails as read, default true)
"""

import os
import sys
import json
import time
import base64
import tempfile
from pathlib import Path
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()

# ── Path setup ────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.graph import run_pipeline
from db.database import init_db, get_all_customers
from inbox.trigger import RESULTS_DIR
from core.logging_config import get_logger

log = get_logger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
CREDENTIALS_PATH  = ROOT / os.getenv("GMAIL_CREDENTIALS_PATH", "credentials.json")
TOKEN_PATH        = ROOT / os.getenv("GMAIL_TOKEN_PATH", "gmail_token.json")
WATCH_LABEL       = os.getenv("GMAIL_LABEL", "INBOX")
POLL_INTERVAL     = int(os.getenv("GMAIL_POLL_INTERVAL", "10"))
SENDER_FILTER     = os.getenv("GMAIL_SENDER_FILTER", "")        # e.g. "@supplier.com"
MARK_READ         = os.getenv("GMAIL_MARK_READ", "true").lower() == "true"

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

ATTACHMENT_EXTS = {".pdf", ".jpg", ".jpeg", ".png", ".tiff", ".tif"}

# ── Gmail auth ────────────────────────────────────────────────────────────────

def get_gmail_service():
    """Authenticate and return a Gmail API service object."""
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    creds = None

    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CREDENTIALS_PATH.exists():
                raise FileNotFoundError(
                    f"Gmail credentials not found at {CREDENTIALS_PATH}.\n"
                    "Download credentials.json from Google Cloud Console:\n"
                    "  Console → APIs & Services → Credentials → OAuth 2.0 Client IDs\n"
                    "  Download JSON → save as credentials.json in the project root."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_PATH), SCOPES)
            creds = flow.run_local_server(port=0)

        with open(TOKEN_PATH, "w") as f:
            f.write(creds.to_json())
        log.info("Gmail token saved to %s", TOKEN_PATH)

    return build("gmail", "v1", credentials=creds)


# ── Customer router ───────────────────────────────────────────────────────────

def build_sender_customer_map() -> dict:
    """
    Build a mapping of sender email domain → customer_id.
    Customers are seeded with an 'email_domain' field if present,
    otherwise falls back to fuzzy name matching against the sender address.

    You can also hardcode mappings in .env:
      GMAIL_CUSTOMER_MAP=acme-exports.com:CUST001,fastfreight.net:CUST002
    """
    mapping = {}

    # Load hardcoded mappings from .env first
    env_map = os.getenv("GMAIL_CUSTOMER_MAP", "")
    if env_map:
        for pair in env_map.split(","):
            pair = pair.strip()
            if ":" in pair:
                domain, cid = pair.split(":", 1)
                mapping[domain.strip().lower()] = cid.strip()

    # Auto-map from DB customers (name → fuzzy domain guess)
    try:
        customers = get_all_customers()
        for c in customers:
            # Use explicit email_domain field if present
            if c.get("email_domain"):
                mapping[c["email_domain"].lower()] = c["id"]
            else:
                # Fuzzy: turn "AutoParts Express" → "autoparts" as a partial domain match key
                name_slug = c["name"].lower().replace(" ", "").replace("-", "")
                mapping[name_slug] = c["id"]
    except Exception:
        pass

    return mapping


def resolve_customer(sender_email: str, subject: str, mapping: dict) -> str:
    """
    Resolve customer_id from sender email and subject line.
    Priority:
      1. Exact sender domain match in mapping
      2. Partial domain match (sender domain contains a mapping key)
      3. Subject line contains a customer name slug
      4. Default to first customer (CUST001) with a warning
    """
    sender_lower  = sender_email.lower()
    subject_lower = subject.lower()

    # Extract domain from sender
    sender_domain = sender_lower.split("@")[-1] if "@" in sender_lower else sender_lower

    # 1. Exact domain match
    if sender_domain in mapping:
        return mapping[sender_domain]

    # 2. Partial domain match — e.g. "mail.acme-exports.com" matches "acme-exports.com"
    for key, cid in mapping.items():
        if key in sender_domain or key in sender_lower:
            return cid

    # 3. Subject line match
    for key, cid in mapping.items():
        if key in subject_lower:
            return cid

    # 4. Default with warning
    try:
        customers = get_all_customers()
        if customers:
            default_id = customers[0]["id"]
            log.warning("Could not resolve customer from '%s' — defaulting to %s", sender_email, default_id)
            return default_id
    except Exception:
        pass

    return "CUST001"


# ── Email processing ──────────────────────────────────────────────────────────

def get_message_headers(msg: dict) -> dict:
    """Extract From, Subject, Date from message headers."""
    headers = {}
    for h in msg.get("payload", {}).get("headers", []):
        headers[h["name"].lower()] = h["value"]
    return headers


def download_attachments(service, msg_id: str, payload: dict, tmp_dir: str) -> list:
    """
    Recursively find and download PDF/image attachments from a message.
    Returns list of (tmp_path, original_filename) tuples.
    """
    docs = []

    def _process_part(part):
        filename = part.get("filename", "")
        mime     = part.get("mimeType", "")
        body     = part.get("body", {})

        if filename and Path(filename).suffix.lower() in ATTACHMENT_EXTS:
            att_id = body.get("attachmentId")
            data   = body.get("data")

            if att_id:
                # Fetch attachment data from Gmail API
                att = service.users().messages().attachments().get(
                    userId="me", messageId=msg_id, id=att_id
                ).execute()
                data = att.get("data", "")

            if data:
                file_bytes = base64.urlsafe_b64decode(data + "==")
                suffix     = Path(filename).suffix.lower()
                tmp_path   = os.path.join(tmp_dir, filename)
                with open(tmp_path, "wb") as f:
                    f.write(file_bytes)
                docs.append((tmp_path, filename))
                log.info("Downloaded attachment: %s (%s bytes)", filename, f"{len(file_bytes):,}")

        # Recurse into multipart
        for sub in part.get("parts", []):
            _process_part(sub)

    _process_part(payload)
    return docs


def process_gmail_message(service, msg_id: str, customer_map: dict) -> dict | None:
    """
    Fully process one Gmail message:
      headers → attachments → resolve customer → run pipeline → store result
    Returns result dict or None if no valid attachments found.
    """
    msg     = service.users().messages().get(userId="me", id=msg_id, format="full").execute()
    headers = get_message_headers(msg)

    sender  = headers.get("from", "unknown@supplier.com")
    subject = headers.get("subject", "(no subject)")
    date    = headers.get("date", datetime.utcnow().isoformat())

    log.info("New email: '%s' from %s", subject, sender)

    with tempfile.TemporaryDirectory() as tmp_dir:
        docs = download_attachments(service, msg_id, msg.get("payload", {}), tmp_dir)

        if not docs:
            log.warning("No valid attachments found in '%s' — skipping.", subject)
            # Still mark as read so we don't reprocess
            if MARK_READ:
                service.users().messages().modify(
                    userId="me", id=msg_id, body={"removeLabelIds": ["UNREAD"]}
                ).execute()
            return None

        customer_id = resolve_customer(sender, subject, customer_map)
        log.info("Resolved customer: %s | Docs: %d", customer_id, len(docs))

        # Run the pipeline (attachments still in tmp_dir at this point)
        result = run_pipeline(docs, customer_id)

        # Index ALL docs for RAG as one shipment store, while temp files still exist.
        # (Must happen inside the TemporaryDirectory block — the files are gone after.)
        try:
            from rag.retriever import index_documents
            index_documents([p for p, _ in docs], result.get("shipment_id", ""))
        except Exception as rag_err:
            log.warning("RAG indexing skipped for shipment %s: %s",
                        result.get("shipment_id"), rag_err)

    # Attach email metadata
    result["email_from"]    = sender
    result["email_subject"] = subject
    result["email_date"]    = date
    result["received_at"]   = datetime.utcnow().isoformat()
    result["gmail_msg_id"]  = msg_id

    # Persist result JSON
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    result_file = RESULTS_DIR / f"{result['shipment_id']}.json"
    with open(result_file, "w") as f:
        json.dump(result, f, indent=2, default=str)

    # Mark email as read
    if MARK_READ:
        service.users().messages().modify(
            userId="me", id=msg_id, body={"removeLabelIds": ["UNREAD"]}
        ).execute()
        log.info("Marked message %s as read.", msg_id)

    log.info("Shipment %s -> %s", result["shipment_id"], result["decision"].upper())
    return result


# ── Poller ────────────────────────────────────────────────────────────────────

def watch_gmail(poll_interval: float = POLL_INTERVAL):
    """
    Poll Gmail every `poll_interval` seconds for new unread emails with attachments.
    Processes each new email through the full pipeline.
    """
    init_db()
    log.info("Starting Gmail inbox watcher (polling every %ss | label: %s)", poll_interval, WATCH_LABEL)
    if SENDER_FILTER:
        log.info("Sender filter: %s", SENDER_FILTER)
    log.info("Authenticate once in browser, then runs automatically.")

    service      = get_gmail_service()
    customer_map = build_sender_customer_map()
    processed    = set()

    log.info("Authenticated. Watching for new emails...")

    while True:
        try:
            # Build query: unread + label + optional sender filter
            query = "is:unread has:attachment"
            if SENDER_FILTER:
                query += f" from:{SENDER_FILTER}"

            results = service.users().messages().list(
                userId="me", labelIds=[WATCH_LABEL], q=query, maxResults=20
            ).execute()

            messages = results.get("messages", [])

            for msg_ref in messages:
                msg_id = msg_ref["id"]
                if msg_id in processed:
                    continue
                processed.add(msg_id)

                try:
                    process_gmail_message(service, msg_id, customer_map)
                except Exception as e:
                    log.error("Failed to process message %s: %s", msg_id, e, exc_info=True)

        except Exception as e:
            log.error("Gmail poll error: %s", e, exc_info=True)

        time.sleep(poll_interval)


if __name__ == "__main__":
    watch_gmail()