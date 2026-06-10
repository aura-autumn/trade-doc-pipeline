# Trade Document Pipeline — GoComet Nova

Multi-agent system for trade document extraction, validation, and routing. Built with LangGraph, SQLite, ChromaDB, and Streamlit.

## Architecture

```
PDF/Image → [Extractor] → [Validator] → [Router] → SQLite
                                                        ↓
                                            NL Query (Text-to-SQL + RAG)
                                                        ↓
                                                 Streamlit UI
```

**Three LangGraph agents:**

- **Extractor** — pdfplumber → Tesseract OCR → Vision LLM (layered fallback). Outputs per-field confidence scores.
- **Validator** — rule-based per customer. Five statuses: `match | mismatch | uncertain | missing | not_checked`. `confidence < 0.6` is always `uncertain`. Fields with no rule are `not_checked`, never dropped silently.
- **Router** — rule-based decision (`auto_approve / flag_for_review / draft_amendment`). LLM writes the reasoning text and draft email only. Extraction context (invoice number, consignee, ports) is passed through so emails are shipment-specific.

---

## Setup

### 1. Install dependencies

```bash
cd trade-doc-pipeline
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
```

**Groq (default — free, no credit card):**
```
LLM_PROVIDER=groq
GROQ_API_KEY=your_key_here
```
Get a free key at https://console.groq.com

**Gemini (free tier):**
```
LLM_PROVIDER=gemini
GEMINI_API_KEY=your_key_here
```
Get a free key at https://aistudio.google.com

**OpenAI:**
```
LLM_PROVIDER=openai
OPENAI_API_KEY=your_key_here
```

**Ollama (fully local, no API key):**
```bash
ollama pull llava        # vision
ollama pull llama3.2     # text
```
```
LLM_PROVIDER=ollama
```

### 3. (Windows) Install Tesseract

Download from https://github.com/UB-Mannheim/tesseract/wiki and install to `C:\Program Files\Tesseract-OCR\`. The extractor auto-detects it.

### 4. Initialize database

```bash
python -m db.database
```

Seeds 5 demo customers with rules.

### 5. Run the UI

```bash
streamlit run ui/app.py
```

Open http://localhost:8501

---

## Usage

1. Go to **▶ Run Pipeline**
2. Select a customer
3. Upload a trade document (PDF or image)
4. Click **▶ Run Pipeline**
5. Review extraction, validation, decision, and draft email across the tabs
6. Use **🔎 RAG Lookup** to ask questions about the document content

---

## CLI

```bash
python -m pipeline.graph <doc_path> <customer_id>
# Example:
python -m pipeline.graph data/sample_docs/invoice.pdf CUST001
```

---

## Eval

```bash
python -m eval.eval
```

Add test documents to `./data/sample_docs/` and ground truth labels to `./data/ground_truth.json` first.

---

## Demo Customers

| ID | Name | Key Rules |
|---|---|---|
| CUST001 | Acme Imports Ltd | Incoterms=CIF, HS 8471x, POD=Nhava Sheva |
| CUST002 | Global Tech Distributors | Incoterms=FOB, HS 8542x, POL=Shanghai |
| CUST003 | MediSupply Chain Co | Incoterms=DDP, HS 3004x, POD=Mumbai |
| CUST004 | FastFashion Retail | Incoterms=CFR, HS 6109x, POD=Chennai |
| CUST005 | AutoParts Express | Incoterms=EXW, HS 8708x |

---

## Project Structure

```
trade-doc-pipeline/
├── db/
│   ├── schema.sql
│   └── database.py         # init, seed, all CRUD helpers
├── llm/
│   └── client.py           # swappable LLM client (groq/gemini/openai/ollama)
├── agents/
│   ├── extractor.py        # pdfplumber → OCR → vision LLM
│   ├── validator.py        # rule-based validation + get_validation_summary()
│   └── router.py           # decision logic + LLM reasoning + draft email
├── pipeline/
│   └── graph.py            # LangGraph state graph + SQLite checkpointer
├── rag/
│   └── retriever.py        # ChromaDB indexing + RAG query
├── query/
│   └── nl_query.py         # Text-to-SQL + RAG query routing
├── eval/
│   └── eval.py             # offline evaluation script
├── ui/
│   └── app.py              # Streamlit UI (5 pages)
└── data/
    ├── chroma/             # ChromaDB vector store (auto-created)
    ├── trade_docs.db       # main SQLite DB (auto-created)
    ├── sample_docs/        # put test documents here
    └── ground_truth.json   # eval labels
```

---

## Design Decisions

**Why rule-based routing, not LLM routing?**
The decision logic (`auto_approve / flag_for_review / draft_amendment`) is deterministic and auditable. LLM is only used to write the human-readable reasoning and email — both of which a human reviews before any action is taken.

**Why three extraction layers?**
pdfplumber is fast and exact for native-text PDFs. Tesseract handles scans. Vision LLM is the last resort — slowest and most expensive. Most production docs never reach the LLM layer.

**Why LangGraph?**
SQLite checkpointer means if the pipeline crashes mid-run (LLM timeout, DB error), it can resume from the last successful node on retry rather than restarting from scratch.

**Why explicit SentenceTransformer for ChromaDB?**
ChromaDB's default embedding uses `onnxruntime` internally via a non-standard import path that fails on Windows even when `onnxruntime` is installed. Passing `SentenceTransformerEmbeddingFunction` explicitly bypasses this entirely — same model, same quality.

**Why Text-to-SQL + RAG, not just one?**
Text-to-SQL answers structured aggregate questions ("how many flagged this week", "which customers had amendments"). RAG answers document-content questions ("what does the doc say about the consignee address"). They serve different query shapes.

**Confidence threshold:**
`< 0.6` = uncertain. Non-negotiable. Uncertain fields are always surfaced and never silently approved, even if the extracted value would technically match the rule.