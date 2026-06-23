"""
Natural Language Query Layer
- Text-to-SQL for structured shipment data queries
- Falls back to RAG for document-specific questions
- Non-engineers can ask questions in plain English
"""

import json
import re
from db.database import run_raw_query, get_schema_description
from llm.client import get_llm
from core.logging_config import get_logger

log = get_logger(__name__)


TEXT_TO_SQL_PROMPT = """You are a SQL expert for a trade document validation system.
Convert the user's question into a valid SQLite SELECT query.

Database schema:
{schema}

Rules:
1. Only generate SELECT statements. Never INSERT, UPDATE, DELETE, DROP.
2. Use proper JOINs when needed.
3. For date filters, use: datetime('now', '-7 days') for "this week", etc.
4. Always LIMIT results to 100 unless user specifies otherwise.
5. Use readable column aliases.
6. Return ONLY the SQL query, nothing else. No markdown, no explanation.

Question: {question}

SQL:"""


def _clean_sql(sql: str) -> str:
    """Strip markdown, extra whitespace from LLM SQL output."""
    sql = sql.strip()
    sql = re.sub(r"```sql\s*", "", sql)
    sql = re.sub(r"```\s*", "", sql)
    sql = sql.strip().rstrip(";") + ";"
    return sql


def _is_document_question(question: str) -> bool:
    """
    Determine if question needs RAG (document-level) vs SQL (database-level).
    Heuristic: questions about specific field content, source text, etc.
    """
    doc_keywords = [
        "in the document", "source", "snippet", "text", "says", "written",
        "shows", "mention", "field value", "extracted from"
    ]
    q_lower = question.lower()
    return any(kw in q_lower for kw in doc_keywords)


def run_text_to_sql(question: str) -> dict:
    """
    Convert NL question to SQL and run it.
    Returns: {sql, results, answer, error}
    """
    llm = get_llm(vision=False)
    schema = get_schema_description()

    prompt = TEXT_TO_SQL_PROMPT.format(schema=schema, question=question)

    try:
        response = llm.invoke(prompt)
        raw_sql = response.content if hasattr(response, "content") else str(response)
        sql = _clean_sql(raw_sql)
        log.info("Text-to-SQL | q=%r | sql=%s", question, sql)

        # Safety check
        if any(kw in sql.upper() for kw in ["INSERT", "UPDATE", "DELETE", "DROP", "ALTER"]):
            log.warning("Blocked unsafe SQL generated for question %r: %s", question, sql)
            return {
                "sql": sql,
                "results": [],
                "answer": "This query type is not allowed for safety reasons.",
                "error": "Unsafe SQL detected"
            }

        rows = run_raw_query(sql)

        # Natural language answer from results
        answer = _results_to_natural_language(question, sql, rows)

        return {
            "sql": sql,
            "results": rows,
            "answer": answer,
            "error": None,
        }

    except Exception as e:
        log.error("Text-to-SQL failed for question %r: %s", question, e)
        return {
            "sql": "",
            "results": [],
            "answer": f"Could not answer: {str(e)}",
            "error": str(e),
        }


def _results_to_natural_language(question: str, sql: str, rows: list[dict]) -> str:
    """Convert SQL results to a natural language answer."""
    if not rows:
        return "No results found for that query."

    if len(rows) == 1 and len(rows[0]) == 1:
        # Single value result (COUNT, SUM, etc.)
        val = list(rows[0].values())[0]
        return f"{val}"

    # For multi-row results, summarize
    llm = get_llm(vision=False)
    results_text = json.dumps(rows[:20], indent=2, default=str)  # limit to 20 rows for context

    prompt = f"""Given this question and SQL query results, provide a concise natural language answer.

Question: {question}
Results ({len(rows)} rows):
{results_text}

Answer in 1-3 sentences. Be specific with numbers. If it's a list, summarize the key items."""

    try:
        response = llm.invoke(prompt)
        return response.content if hasattr(response, "content") else str(response)
    except Exception:
        return f"Found {len(rows)} result(s)."


def run_nl_query(question: str, shipment_id: str = None) -> dict:
    """
    Main entry point for natural language queries.
    Routes to Text-to-SQL or RAG based on question type.
    """
    if shipment_id and _is_document_question(question):
        # RAG path: answer from document content
        from rag.retriever import answer_with_rag
        answer = answer_with_rag(question, shipment_id)
        return {
            "type": "rag",
            "answer": answer,
            "sql": None,
            "results": [],
            "error": None,
        }
    else:
        # SQL path: answer from database
        result = run_text_to_sql(question)
        result["type"] = "sql"
        return result


# Pre-built example queries shown in UI
EXAMPLE_QUERIES = [
    "How many shipments were flagged this week?",
    "Show all shipments pending review",
    "Which customer had the most mismatches?",
    "What is the auto-approval rate this month?",
    "List all shipments with critical field mismatches",
    "How many documents were processed today?",
    "Show shipments with HS code mismatches",
    "What fields are most commonly mismatched?",
]


if __name__ == "__main__":
    from db.database import init_db, seed_demo_customers
    init_db()
    seed_demo_customers()

    questions = [
        "How many customers are in the system?",
        "Show all shipments",
        "How many rules does CUST001 have?",
    ]
    for q in questions:
        print(f"\nQ: {q}")
        result = run_nl_query(q)
        print(f"SQL: {result.get('sql')}")
        print(f"A: {result.get('answer')}")
