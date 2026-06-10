# Trade Document Pipeline

Multi-agent pipeline for trade document extraction, validation, and routing.

## Architecture

```
Upload (PDF/Image) → Extractor → Validator → Router → SQLite
                                                  ↓
                                         NL Query (Text-to-SQL + RAG)
                                                  ↓
                                           Streamlit UI
```

**Three LangGraph agents:**
- **Extractor**: vision LLM extracts 8 fields with per-field confidence. Text fallback for PDFs when vision fails.
- **Validator**: rule-based validation per customer (exact / contains / regex / not_null). Cross-document reconciliation for multi-doc shipments. Never silently approves uncertain fields (confidence < 0.6).
- **Router**: rule-based decision (auto_approve / flag_for_review / draft_amendment) + LLM-generated reasoning and draft amendment email.

## Setup

```bash
git clone <repo>
cd trade-doc-pipeline
pip install -r requirements.txt
cp .env.example .env        # set LLM_PROVIDER and API key. Deafult: groq. (code is configured for the same)
python -m db.database       # init DB + seed 5 demo customers (will run on streamlit run as well. can skip)
python generate_samples.py  # create sample test PDFs
streamlit run ui/app.py
```
## System Dependencies

### Tesseract-OCR (Required for scanned PDFs)
Used for PDF OCR fallback when the text layer is empty or poor quality. Install from:
- **Windows:** https://github.com/UB-Mannheim/tesseract/wiki (installer sets PATH automatically in many cases)
- **Mac:** `brew install tesseract`
- **Linux:** `sudo apt-get install tesseract-ocr`

**Verify:**
```bash 
tesseract --version
```

The pipeline auto-detects Tesseract at common install locations. If detection fails, set `TESSERACT_CMD` manually or reinstall.

## LLM Providers

| Provider | Cost | Vision | Setup |
|---|---|---|---|
| `groq` | Free | Via LLaMA 4 Scout | [console.groq.com](https://console.groq.com) no credit card |
| `gemini` | Free tier | Yes | [aistudio.google.com](https://aistudio.google.com) |
| `openai` | Paid | Yes | OpenAI API key |
| `ollama` | Free (local) | llava | `ollama pull llava && ollama pull llama3.2` |

Set in `.env`:
```
LLM_PROVIDER=groq
GROQ_API_KEY=your_key_here
```

## Usage

1. Open http://localhost:8501
2. **Run Pipeline** : select customer, upload one or more trade docs, click Run
3. **Shipment History** : browse past runs, see per-field issues
4. **Query Layer** : ask natural language questions ("how many flagged this week?")
5. **Manage Customers** : add customers and define validation rules
6. **Eval** : run offline accuracy evaluation

## Multi-Document Support

Upload multiple files (BOL + Commercial Invoice + Packing List) for one shipment. The validator cross-checks fields like consignee, HS code, and gross weight across all documents and flags inconsistencies.

## Project Structure

```
trade-doc-pipeline/
├── db/
│   ├── schema.sql          # SQLite schema
│   └── database.py         # DB helpers + 5 seeded demo customers
├── llm/
│   └── client.py           # Swappable LLM client
├── agents/
│   ├── extractor.py        # Vision LLM + text fallback + confidence scoring
│   ├── validator.py        # Rule engine + cross-doc reconciliation
│   └── router.py           # Decision + LLM draft email
├── pipeline/
│   └── graph.py            # LangGraph state graph + run_pipeline()
├── rag/
│   └── retriever.py        # FAISS + TF-IDF/SVD (no torch dependency)
├── query/
│   └── nl_query.py         # Text-to-SQL + RAG query routing
├── eval/
│   └── eval.py             # Offline evaluation script
├── ui/
│   └── app.py              # Streamlit UI
└── data/
    ├── sample_docs/        # Add test documents here
    └── ground_truth.json   # Labels for eval
```

## Demo Customers (pre-seeded)

| ID | Customer | Key Rules |
|---|---|---|
| CUST001 | Global Freight Corp | CIF, Shanghai, HS not null |
| CUST002 | Apex Logistics LLC | FOB or EXW, consignee match |
| CUST003 | Zenith Trading | FOB, Rotterdam discharge |
| CUST004 | Oceanic Ventures | DDP or DAP, invoice required |
| CUST005 | AutoParts Express | EXW, HS 8708x, consignee match |

## Design Decisions

**Why three agents?** Each has a distinct failure mode. Extractor fails on bad docs. Validator fails on wrong rules. Router fails on ambiguous logic. Separate agents = independent debuggability, replaceability, and eval.

**Why LangGraph?** Explicit state, crash recovery via checkpointer, conditional edges for error routing. State is visible at every step.

**Why FAISS over ChromaDB?** ChromaDB pulls `onnxruntime` which has DLL issues on Windows. FAISS + scikit-learn TF-IDF is lightweight, zero system dependencies, runs anywhere.

**Why Text-to-SQL + RAG?** Text-to-SQL answers structured questions about the database ("how many flagged"). RAG answers document-content questions ("what does the doc say about consignee"). Different query types, different tools.

**Confidence threshold:** `< 0.6` = uncertain. Uncertain fields are always surfaced and never silently approved.