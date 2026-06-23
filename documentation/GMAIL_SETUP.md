# Gmail / Inbox Trigger Setup — Part 2

Part 2's key insight is that **the trigger — not the model — is the missing piece**:
the agent must wake up when SU's email arrives, not when someone uploads a file.
This repo ships **three ways to trigger the pipeline**, from easiest-to-demo to
most-realistic:

| Mode | Command | What it proves | Needs Gmail OAuth? |
|------|---------|----------------|--------------------|
| **A. Folder watcher** (simulated inbox) | `python -m inbox.trigger` | The agent wakes on email *arrival*, decoupled from the UI | No |
| **B. In-UI "Simulate SU Email"** | (React Incoming page) | Same chain, run inline for a quick click-through | No |
| **C. Real Gmail watcher** | `python -m inbox.gmail_trigger` | The real plumbing — polls a live inbox over OAuth | Yes |

The assignment explicitly says *"Watch a folder or simulate an inbox… mock the
email plumbing — the logic is what matters."* **Mode A** demonstrates the trigger
firing on arrival with no external setup. **Mode C** wires the identical logic to a
real Gmail account.

---

## ▶ End-to-end walkthrough (Mode A — simulated inbox)

```bash
# Terminal 1 — backend API
uvicorn api.main:app --reload --port 8000

# Terminal 2 — React UI
cd frontend && npm run dev          # → http://localhost:5173

# Terminal 3 — the watcher (agent wakes on email arrival)
python -m inbox.trigger

# Terminal 4 — SU "sends" an email: drop a sample into the watched folder
cp inbox/sample_emails/email_multi_doc.json inbox/incoming/
```

What happens, step by step:
1. **Terminal 3** logs the email arriving → `Extracting … → Validating … → Routing …`
   → `Shipment <id> -> AMENDMENT_DRAFTED`. This is the trigger: nothing was uploaded
   in the UI; the pipeline activated on email arrival.
2. The processed shipment is written to SQLite and to **`inbox/results/`**.
3. React UI → **Incoming** page → **📡 Inbox Watcher Feed** → the new shipment
   appears → **View** shows the field-by-field verification, the cross-document
   inconsistency banner (this sample has 3 docs), and the editable draft reply.
   **Mark as Sent** records the reply — the agent never sends on its own.
4. **Query** page → *"show everything pending review for Zenith Trading"* → the
   chain from email → verified output → query is alive.

Outcomes by sample: `email_clean.json` → auto-approve, `email_mismatch.json` →
amendment, `email_multi_doc.json` → cross-doc check fires.

---

## Gmail (Mode C) — watch a real inbox

The Gmail trigger watches a real Gmail inbox for incoming SU emails with PDF attachments and fires the same pipeline automatically. Setup takes ~5 minutes.

---

## Step 1 — Google Cloud Project

1. Go to [https://console.cloud.google.com]
2. Create a new project (or use an existing one) — name it anything, e.g. `trade-pipeline`
3. In the left sidebar: **APIs & Services → Library**
4. Search for **Gmail API** → click Enable

---

## Step 2 — OAuth Credentials

1. **APIs & Services → Credentials → Create Credentials → OAuth 2.0 Client ID**
2. If prompted, configure the OAuth consent screen first:
   - User type: **External**
   - App name: anything (e.g. `Nova CG`)
   - Add your Gmail address as a test user
   - Scopes: add `https://www.googleapis.com/auth/gmail.modify`
3. Back in Create Credentials:
   - Application type: **Desktop App**
   - Name: anything
4. Click **Download JSON** → save as `credentials.json` in the **project root** (same folder as `requirements.txt`)

---

## Step 3 — Configure Customer Routing (optional but recommended)

Add to your `.env` file to map supplier email domains to customer IDs:

```env
GMAIL_CUSTOMER_MAP=acme-exports.com:CUST001,fastfreight.net:CUST002,zenith-trading.com:CUST003
GMAIL_SENDER_FILTER=          # leave blank to process all senders, or set e.g. @supplier.com
GMAIL_POLL_INTERVAL=10        # seconds between inbox checks (default: 10)
GMAIL_MARK_READ=true          # mark processed emails as read (default: true)
```

If `GMAIL_CUSTOMER_MAP` is not set, the system auto-routes based on fuzzy sender domain matching against customer names in your database.

---

## Step 4 — First Run (browser auth)

```bash
python -m inbox.gmail_trigger
```

On first run, a browser window opens asking you to authorise Gmail access. Sign in with your Gmail account and allow the requested permissions. A `gmail_token.json` file is saved — all future runs use this token automatically with no re-auth needed.

---

## Step 5 — Send a Test Email

Send an email **to your own Gmail inbox** (from any address) with:
- Subject: `Shipment Documents for [Customer Name]`
- A PDF attachment (use any of the sample docs in `data/sample_docs/`)

Within `GMAIL_POLL_INTERVAL` seconds, the pipeline fires automatically. The result appears in the CG UI under **📡 Inbox Watcher Feed** on the Incoming page.

---

## Running in Production Mode

```bash
# Terminal 1 — Gmail watcher (runs continuously)
python -m inbox.gmail_trigger

# Terminal 2 — backend API
uvicorn api.main:app --port 8000

# Terminal 3 — CG Verification UI (React)
cd frontend && npm run dev
```

The sidebar in the CG UI shows Gmail connection status:
- 🟢 Connected — `gmail_token.json` exists, watcher has authenticated
- 🟡 Ready — `credentials.json` exists but watcher not yet run
- ⚪ Not configured — `credentials.json` missing, setup needed

---

## Fallback — No Gmail

If you prefer not to set up Gmail OAuth, use the folder watcher instead:

```bash
# Terminal 1
python -m inbox.trigger

# Terminal 2 — drop an email JSON to trigger
cp inbox/sample_emails/email_clean.json inbox/incoming/
```

Or use the **"Simulate SU Email Arrival"** form directly in the CG UI — upload any PDF and the pipeline runs immediately in the UI without needing any watcher process.

---

## Security Notes

- `credentials.json` and `gmail_token.json` are in `.gitignore` — never commit them
- The OAuth scope is `gmail.modify` (read + mark as read) — the agent never sends email
- All email processing is local — no data leaves your machine except to your configured LLM API
