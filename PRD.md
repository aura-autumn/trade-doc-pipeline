# Product Requirement Document: Multi-Agent Trade Document Pipeline

---

## 1 | Understanding Nova

**What is Nova? What can it solve that traditional SaaS can't?**
Nova is GoComet's platform for shipping AI-agent systems that *do* an operational job end-to-end, not just store or surface it. Traditional SaaS gives you a system of record, a database with a UI, and still expects a human to read the document, apply the judgment, and type the reply. Its unit of value is structured data; it has no opinion and takes no action. Nova inverts that: the software does the boring 80% of operational judgment (extract → validate → decide → draft) and escalates only exceptions. Traditional SaaS can't close this gap because trade validation isn't a data-entry problem, it's a tacit-rules-and-judgment problem that lives in people's heads and varies per customer. A config screen can't encode "this customer is always CIF except on these lanes." Nova's unit of value is the completed *outcome*: a validated shipment, a ready-to-send amendment. *(~150 words)*

**What is the FDE model and why does GoComet use it for Nova?**
A Forward Deployed Engineer embeds with the customer, learns the real workflow including the rules that "live in someone's head" and tunes the agent system against that reality instead of shipping a generic product and hoping it fits. GoComet uses it for Nova because no two CG teams validate identically: rule sets, document formats, and exception patterns all differ, and most of that knowledge is tacit and undocumented. A horizontal product can't capture it from a settings page. The FDE sits close enough to the operator to encode those rules, watch the agent fail on the customer's *actual* messy PDFs, and iterate in days. That's the difference between a demo that works on clean invoices and a system that survives the 2–4 cycle email reality. The FDE makes the outcome real for one customer, then that hardened configuration becomes a repeatable template for the next. *(~155 words)*

**What does "System of Outcomes" mean vs Record vs Engagement?**
A **System of Record** stores truth as the database of shipments; it's judged on data integrity. A **System of Engagement** helps a human act on that truth with dashboards, inboxes, notifications; it's judged on usage and clicks. A **System of Outcomes** owns the *result*: it doesn't just store the shipment or notify you a doc arrived, it extracts the fields, checks them against the customer's rules, decides, and drafts the reply, so the validated document set is produced with the human handling only exceptions. The distinction is accountability for the end state. Record asks "is the data correct?"; Engagement asks "did the human see it?"; Outcomes asks "did the job actually get done, correctly?", measured in shipments cleared, amendment cycles removed, and errors caught before customs. It moves software from *the place work is tracked* to *the thing that does the work*. *(~150 words)*

---

## 2 | Problem Statement

**Where the current trade-doc validation flow breaks (named failure modes):**

1. **Tacit rules.** Customer requirements (Incoterms, HS-code ranges, consignee naming, mandatory ports) live in a senior validator's head. New CG hires make mistakes for weeks; no two reviewers validate identically.
2. **Manual field reading.** Every field of every PDF is read by eye. It's slow, fatigue-prone, and error rates rise with backlog pressure.
3. **Multi-cycle amendment loops.** When a field is wrong, CG hand-types an amendment email; SU fixes and resubmits. 2–4 cycles is normal, each adding **4–24 hrs** of delay.
4. **No cross-document consistency check.** Consignee / HS code / gross weight can disagree between the BOL, Invoice, and Packing List and still slip through, because each doc is read in isolation.
5. **Zero visibility & no audit trail.** Nobody can answer "how many shipments are pending for customer X?" without asking around, and there's no defensible record if a dispute arises later.
6. **High cost of a miss.** A wrong HS code or mismatched consignee that reaches the customer means customs holds, cargo delays, or contract penalties.

**Success for a CG operator in their first 5 minutes:**
They open the tool, pick the customer, and upload a shipment's documents. Within seconds they see **every field extracted with a per-field confidence**, a **field-by-field validation result** (match / mismatch / uncertain) showing *what was found vs what the customer expects*, a **decision** (auto-approve / flag / draft amendment) **with the agent's reasoning**, and if needed a **ready-to-edit amendment email** listing each discrepancy. They never read the raw PDF, and they can ask in plain English *"what's pending review for customer X?"* and get a grounded answer. The win: trust that nothing wrong was silently approved, and the 30-minute read-and-type job is now a 30-second review-and-send.

---

## 3 | Users + Jobs-to-be-Done

**Persona A: CG operator (validator).** *"Priya, Cargo/Control Group analyst."* Receives SU's documents, cross-checks every field against customer requirements, and replies approved or here's-what-to-fix. Cares about: **never missing a wrong field** (her name is on the approval), clearing the backlog fast, and not getting blamed for a customs hold. Fears a silent wrong-approval more than a false flag.

**Persona B: SU supplier (shipper).** *"Rajesh, shipping coordinator."* Generates and emails the document set; his job "feels done once the email is sent." Cares about: **getting docs accepted on the first try**, fewer back-and-forth cycles, and *specific* feedback on exactly what to fix.

**Jobs-to-be-Done:**
1. **When** a new shipment's documents arrive, **I want** every field extracted and checked against this customer's rules automatically, **so that** I don't read each PDF line by line.
2. **When** a field is low-confidence or missing, **I want** it surfaced explicitly (never silently passed), **so that** I never unknowingly approve a wrong document.
3. **When** there are discrepancies, **I want** a ready-to-edit amendment email listing field / found / expected, **so that** I reply to SU in seconds instead of typing it out.
4. **When** a shipment has multiple documents (BOL + Invoice + Packing List), **I want** consignee, HS code and weight checked for agreement *across* all of them, **so that** an inter-document mismatch doesn't slip through.
5. **When** I need status, **I want** to ask "how many shipments are pending review for customer X?" in plain English, **so that** I get an answer without writing SQL or pinging engineering.
6. **(SU) When** I submit documents, **I want** clear field-level feedback on what's wrong, **so that** I fix everything in one cycle instead of four.

---

## 4 | Agent Architecture *(technical core)*

### Why three agents and not one prompt, not five?
The boundaries are drawn **where the failure mode and the right tool change**:

- **Not one giant prompt.** A single prompt that perceives the document, applies deterministic customer rules, *and* decides the action is unauditable and untestable. You can't unit-test a rule, you can't tell whether a wrong outcome came from misreading or mis-deciding, and you've handed deterministic logic (does `incoterms == "CIF"`?) to a model that can hallucinate. Worse, it blurs confidence: extraction uncertainty and decision uncertainty become one opaque number.
- **Three agents.** Each owns one **distinct failure mode** and is independently debuggable, replaceable, and evaluable:
  - **Extractor = perception/executor.** Fails on *bad documents* (scans, abbreviations).
  - **Validator = verifier.** Fails on *wrong rules*. Deliberately **deterministic Python, no LLM** so it can never hallucinate a "match."
  - **Router = planner/decider.** Fails on *ambiguous decision logic*. The decision itself is rule-based; the LLM is used only to *explain* and *draft*.
- **Not five.** A separate doc-type classifier and a separate "emailer" agent would add hops, latency and cost without isolating a new failure mode, classification folds into extraction, and drafting is part of the routing decision. We add an agent only when a genuinely different failure mode appears (e.g., Part 2's email-trigger ingestion).

### Responsibilities · Input · Output

| Agent | Responsibility | Input | Output |
|---|---|---|---|
| **Extractor** | Pull 8 structured fields + per-field confidence from any trade doc | File path (PDF/image) | `{field: {value, confidence, method}}` |
| **Validator** | Deterministically check each field vs customer rules; reconcile across docs | Extraction map (per doc) + customer rule set | List of `{field, status∈{match,mismatch,uncertain,missing,not_checked}, found, expected, is_critical, detail}` + cross-doc discrepancies |
| **Router** | Decide auto_approve / flag_for_review / draft_amendment; explain it; draft email | Validation results + summary + flattened extraction | `{decision, reasoning, draft_email}` |

### How the agents talk to each other
**Structured handoff over shared, typed state**, not free-text message passing. The pipeline is a LangGraph `StateGraph` with a typed `PipelineState` (`TypedDict`). Each node reads the keys it needs and returns a partial-state update; LangGraph merges it. Extraction is computed once per document *before* the graph (so multi-doc shipments are handled), then passed in as pre-populated state; the validator and router consume structured dicts, never prose. This keeps every handoff inspectable and serializable.

### How state survives a crash mid-pipeline
Durability is **write-through to SQLite at every stage**, keyed by `shipment_id`:
- Extraction is persisted (`extraction_results`) *before* the graph runs;
- the validator node writes `validation_results` (per-doc and cross-doc) as it completes;
- the router node writes `decisions` and updates `shipments.status`.

So a crash after any node leaves a re-readable, consistent record sp you can resume from persisted rows rather than re-running upstream work. LangGraph's checkpointer (`thread_id = shipment_id`) additionally gives in-process resume. **Honest limitation:** the checkpointer is currently `MemorySaver` (in-memory), so cross-*process* resume relies on the SQLite writes, not the checkpointer. Upgrade path: swap in the SQLite/Postgres checkpointer for durable graph-level recovery (see §8).

---

## 5 | LLM & Tooling Choices *(defend every pick)*

**Which LLM for which agent?**
- **Extractor (text path):** a fast, cheap text LLM Gemini 2.0 Flash (default) / Llama-3.3-70B on Groq (free) / GPT-4o-mini. Extraction from already-OCR'd or native text is a structured-output task where a flagship model is overkill; Flash-tier wins on **cost and latency** with sufficient quality.
- **Extractor (vision fallback):** a **vision-capable** model Gemini 2.0 Flash (multimodal) / GPT-4o / LLaVA (local) invoked when the document is a scan/image or the text layer is empty.
- **Validator:** **no LLM.** Deterministic Python rule engine. This is the most important pick in the system it makes a hallucinated "match" structurally impossible.
- **Router:** the same text LLM, used **only for reasoning + email drafting**. The decision is rule-based.
- **Query layer:** text LLM for Text-to-SQL and for RAG answer synthesis.

**Cost / latency / quality tradeoffs.** Flash-tier text models: ~$0.001–$0.002/doc, ~1–2s/call, ample quality for field extraction and drafting. Vision fallback (GPT-4o-class): 10–50× the cost and the slowest hop, so it's a *fallback*, not the default. `temperature=0` everywhere for determinism/reproducibility. The provider is swappable via `LLM_PROVIDER` (groq | gemini | openai | ollama), so a customer can run fully local (Ollama + LLaVA) if data can't leave their network.

**Which vision model + fallback when the doc is bad quality?** Vision-capable Gemini 2.0 Flash / GPT-4o / LLaVA. The extraction front-end is a **layered fallback** chosen for cost and determinism: native text layer (pdfplumber → PyMuPDF → pdfminer) → render-and-OCR with Tesseract at 300 DPI → **vision LLM** as the last resort for scans the OCR can't read. Native-text PDFs (the common case) never need a paid vision call; genuinely visual/low-quality docs escalate to vision.

**Which orchestration framework and why?** **LangGraph.** It gives explicit, typed state visible at every step, conditional edges for error routing, and a checkpointer abstraction for crash recovery without the magic of higher-level agent frameworks. For a 3-node DAG we want *inspectability and determinism*, not autonomous planning, so a heavyweight autonomous-agent framework would add risk; raw scripts would lose the state/checkpoint story. LangGraph is the right altitude.

**Where structured output / tool use and where to avoid it?**
- **Use it:** Extractor emits strict JSON (`{value, confidence}` per field); Router emits JSON (`reasoning`, `draft_email`); Text-to-SQL is constrained to SELECT-only with a keyword denylist + `LIMIT`.
- **Avoid it:** the **validation rules** and the **routing decision** are plain Python with no LLM, no function-calling. Anything that determines whether a document is *correct* or *approved* must be deterministic and testable, never model-generated.

---

## 6 | Trust, Failure Handling & Evals

**Stopping hallucinated fields.** The extraction prompt is explicit: *"Extract ONLY what is explicitly present. Do NOT infer or guess. If a field is not found, set value to null."* Post-processing caps confidence to ≤0.3 whenever the value is null/empty, so a guessed-but-absent field can never look confident. The downstream validator is deterministic, so even a hallucinated value is checked against the literal rule and surfaced as found-vs-expected. RAG can show the **source snippet** behind any field for grounding.

**Low-confidence extractions (silent approval is the worst answer).** A confidence threshold of **0.6** gates everything: any field below it becomes `uncertain` and a genuinely absent field becomes `missing`. Both **block auto-approve** and route to a human; a *critical* uncertain/missing field forces an amendment. The system is built so the only paths to "approved" are fields that are present, confident, and rule-matching.

**Stopping loops, runaway cost, infinite retries.** The pipeline is a **single-pass DAG** (extractor → validator → router → END) and there are no agent loops to run away. The one retry point (vision fallback) is bounded by Tenacity (`stop_after_attempt(2)`, exponential backoff). Input text is truncated to 8 000 chars per LLM call; `temperature=0`; Text-to-SQL is capped with `LIMIT` and SELECT-only. There is no open-ended "keep trying until it works" anywhere.

**How we'd eval the system.**
- **Offline (implemented):** run the pipeline on a labeled set (`ground_truth.json`) and measure **per-field extraction accuracy**, **decision accuracy**, and **confidence calibration** specifically the *confident-wrong rate* (fields predicted with ≥0.85 confidence that are actually wrong), which is the dangerous quadrant. Reported in the Eval page.
- **Online:** **human-override rate** the share of auto-approvals or drafts a CG operator has to correct. It's the live proxy for whether the system is actually trustworthy in production, and it doubles as a labeling stream for continuous improvement.

---

## 7 | Metrics & Success Criteria

**North-star (one number, one sentence):**
> **Straight-Through Processing (STP) rate**: the percentage of incoming shipments the system clears to a *correct* final decision with **zero human field-reading**, on a rolling 14-day window measured against a human-audited sample.

**Supporting metrics (agent quality · system health · business outcome):**
1. **False-auto-approve rate** *(guardrail)*: % of auto-approvals later found wrong. Target **< 1%**. This caps the north-star.
2. **Extraction field accuracy** vs audited labels (per field).
3. **Confidence calibration** : confident-wrong rate at ≥0.85.
4. **Validation catch rate** : % of true discrepancies the validator flags (recall on mismatches).
5. **Amendment cycles per shipment** : business outcome; target a drop from 2–4 toward 1.
6. **Median time-to-decision** per shipment (doc arrival → decision).
7. **Pipeline success rate & p50/p95 latency** per doc : system health.
8. **Cost per document** (text path vs vision-fallback share).

**Go / No-Go for a 2-week, one-customer pilot.**
- **GO if:** extraction accuracy ≥ ~90% on the customer's *real* documents; false-auto-approve **< 1%** with **zero critical misses** (no wrong HS/consignee/Incoterm auto-approved); CG reports a real time saving; drafts are send-able with ≤1 edit.
- **NO-GO if:** any silent wrong-approval reaches (or would have reached) the customer; CG trust is low enough that they re-read every doc anyway; or latency/cost makes it slower than the manual process.

---

## 8 | What's Next (after Part 1 ships)

If I had two more weeks, I'd build, in order:

1. **The email trigger + human-review queue (Part 2's core).** Today the agent only runs on upload. The highest-leverage next step is making it *wake on SU's email*, handle multi-attachment shipments, and hand CG a queue of verification results + draft replies. This is what turns a demo into the real workflow and it's the missing piece, not the model.
2. **Durable, graph-level recovery.** Replace `MemorySaver` with a SQLite/Postgres checkpointer so a crash mid-pipeline resumes at the graph level, not just via re-readable SQLite rows.
3. **Rule management + versioning UI** with an audit trail, so the FDE/CG can edit customer rules safely and disputes are defensible.
4. **An active-learning loop** from CG overrides every correction becomes a labeled example feeding calibration and prompt/rule tuning.

I'd pick these over, say, a prettier UI or more document types because **trust and the real trigger** are what determine whether the "boring 80%" actually gets automated and everything else is polish on top of an unproven core.
