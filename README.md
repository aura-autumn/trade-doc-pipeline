# Trade Document Pipeline — GoComet Nova

Multi-agent system for trade document extraction, validation, and routing.

## Architecture

```
PDF/Image → Extractor Agent → Validator Agent → Router Agent → SQLite
                                                      ↓
                                              NL Query Layer (Text-to-SQL + RAG)
                                                      ↓
                                              Streamlit UI
```

Three LangGraph agents:
- **Extractor**: Vision LLM extracts 8 structured fields with confidence scores. Docling fallback for low-quality docs.
- **Validator**: Rule-based validation per customer. Never silently approves uncertain fields.
- **Router**: Decides auto_approve / flag_for_review / draft_amendment. LLM generates reasoning and draft email.

## Setup

### 1. Clone and install

```bash
cd trade-doc-pipeline
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env — set your LLM provider and API key
```

**Option A — Gemini (free tier, recommended):**
```
LLM_PROVIDER=gemini
GEMINI_API_KEY=your_key_here
```
Get a free key at https://aistudio.google.com/

**Option B — OpenAI:**
```
LLM_PROVIDER=openai
OPENAI_API_KEY=your_key_here
```

**Option C — Ollama (fully local, no API key):**
```bash
# Install Ollama: https://ollama.ai
ollama pull llava       # vision model
ollama pull llama3.2    # text model
```
```
LLM_PROVIDER=ollama
```

### 3. Initialize database

```bash
python -m db.database
```

This creates the SQLite DB and seeds 5 demo customers with rules.

### 4. Run the UI

```bash
streamlit run ui/app.py
```

Open http://localhost:8501

## Usage

1. Go to **Run Pipeline**
2. Select a customer (5 demo customers are pre-loaded)
3. Upload a trade document (PDF or image)
4. Click **Run Pipeline**
5. View extracted fields, validation results, decision, and draft amendment email

## Run Pipeline from CLI

```bash
python -m pipeline.graph <doc_path> <customer_id>
# Example:
python -m pipeline.graph data/sample_docs/invoice.pdf CUST001
```

## Run Eval

```bash
python -m eval.eval
```

Add test documents to `data/sample_docs/` and ground truth to `data/ground_truth.json`.

## Demo Customers

| ID | Name | Key Rules |
|---|---|---|
| CUST001 | Acme Imports Ltd | Incoterms=CIF, HS 8471x, POD=Nhava Sheva |
| CUST002 | Global Tech Distributors | Incoterms=FOB, HS 8542x, POL=Shanghai |
| CUST003 | MediSupply Chain Co | Incoterms=DDP, HS 3004x, POD=Mumbai |
| CUST004 | FastFashion Retail | Incoterms=CFR, HS 6109x, POD=Chennai |
| CUST005 | AutoParts Express | Incoterms=EXW, HS 8708x |

## Project Structure

```
trade-doc-pipeline/
├── db/
│   ├── schema.sql          # DB schema
│   └── database.py         # DB helpers + seed data
├── llm/
│   └── client.py           # Swappable LLM client (gemini/openai/ollama)
├── agents/
│   ├── extractor.py        # Vision LLM extraction + Docling fallback
│   ├── validator.py        # Rule-based field validation
│   └── router.py           # Decision + draft email generation
├── pipeline/
│   └── graph.py            # LangGraph state graph
├── rag/
│   └── retriever.py        # ChromaDB indexing + RAG queries
├── query/
│   └── nl_query.py         # Text-to-SQL + RAG query routing
├── eval/
│   └── eval.py             # Offline evaluation script
├── ui/
│   └── app.py              # Streamlit UI
└── data/
    ├── sample_docs/        # Add test documents here
    └── ground_truth.json   # Eval ground truth labels
```

## Key Design Decisions

**Why three agents?**
Each has a distinct responsibility and failure mode. Extractor fails on bad docs. Validator fails on wrong rules. Router fails on ambiguous logic. Separating them means you can debug, retrain, and swap each independently.

**Why LangGraph?**
State persistence via SQLite checkpointer. If the pipeline crashes mid-run, it can resume from the last checkpoint. Conditional edges handle error routing cleanly.

**Why Text-to-SQL + RAG, not just one?**
Text-to-SQL answers structured questions ("how many flagged this week"). RAG answers document-content questions ("what does the doc say about consignee"). They serve different query types.

**Why Docling as fallback?**
Docling parses PDF structure (tables, columns) better than pure LLM vision on low-quality scans. Used only when overall confidence is below threshold to control latency and cost.

**Confidence threshold:**
`< 0.6` = uncertain. Uncertain fields are always surfaced and never silently approved. This is non-negotiable.
