# Gmail Integration Setup — Part 2

The Gmail trigger watches a real Gmail inbox for incoming SU emails with PDF attachments and fires the pipeline automatically. Setup takes ~5 minutes.

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

Within `GMAIL_POLL_INTERVAL` seconds, the pipeline fires automatically. The result appears in the CG UI under **📡 Gmail Feed** on the Incoming page.

---

## Running in Production Mode

```bash
# Terminal 1 — Gmail watcher (runs continuously)
python -m inbox.gmail_trigger

# Terminal 2 — CG Verification UI
streamlit run ui/cg_app.py
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
