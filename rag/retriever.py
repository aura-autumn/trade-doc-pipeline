"""
RAG Layer
- Embeds trade documents into ChromaDB
- Answers "where in the doc" questions
- Uses chromadb's default embedding (onnxruntime-based, no torch/transformers needed)
- Text extraction reuses the same robust stack as the extractor
"""

import os
import hashlib
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

CHROMA_PATH = os.getenv("CHROMA_PATH", "./data/chroma")
CHUNK_SIZE   = int(os.getenv("RAG_CHUNK_SIZE", 500))
CHUNK_OVERLAP = int(os.getenv("RAG_CHUNK_OVERLAP", 100))


# ══════════════════════════════════════════════════════════════════════════════
# CHROMA SETUP — default embedding avoids sentence_transformers/torch entirely
# ══════════════════════════════════════════════════════════════════════════════

def _get_chroma_client():
    import chromadb
    os.makedirs(CHROMA_PATH, exist_ok=True)
    return chromadb.PersistentClient(path=CHROMA_PATH)


def _get_collection():
    """
    Uses chromadb's default embedding function (all-MiniLM-L6-v2 via onnxruntime).
    No sentence_transformers, no torch, no transformers package needed.
    First call downloads ~25MB model once and caches it.
    """
    client = _get_chroma_client()
    return client.get_or_create_collection(
        name="trade_documents",
        # No embedding_function arg = chromadb uses its own built-in default
        metadata={"hnsw:space": "cosine"},
    )


# ══════════════════════════════════════════════════════════════════════════════
# TEXT EXTRACTION — same robust stack as extractor.py
# ══════════════════════════════════════════════════════════════════════════════

def _extract_text_from_doc(doc_path: str) -> str:
    """
    Extract text using the same layered approach as the extractor agent.
    Reuses agents.extractor.extract_text to avoid duplication.
    """
    try:
        from agents.extractor import extract_text
        text, method = extract_text(doc_path)
        print(f"[RAG] Text extracted via {method} ({len(text)} chars)")
        return text
    except Exception as e:
        print(f"[RAG] Text extraction failed: {e}")
        return ""


# ══════════════════════════════════════════════════════════════════════════════
# CHUNKING
# ══════════════════════════════════════════════════════════════════════════════

def _chunk_text(text: str) -> list[str]:
    """Split text into overlapping word-based chunks."""
    words = text.split()
    chunks = []
    i = 0
    while i < len(words):
        chunk = " ".join(words[i:i + CHUNK_SIZE])
        chunks.append(chunk)
        i += CHUNK_SIZE - CHUNK_OVERLAP
    return [c for c in chunks if c.strip()]


# ══════════════════════════════════════════════════════════════════════════════
# INDEX
# ══════════════════════════════════════════════════════════════════════════════

def index_document(doc_path: str, shipment_id: str) -> int:
    """
    Index a document into ChromaDB.
    Returns number of new chunks indexed (0 if already indexed).
    """
    text = _extract_text_from_doc(doc_path)
    if not text.strip():
        print(f"[RAG] No text extracted from {doc_path} — skipping index")
        return 0

    chunks = _chunk_text(text)
    collection = _get_collection()
    doc_id = hashlib.md5(doc_path.encode()).hexdigest()[:8]

    ids, documents, metadatas = [], [], []
    for i, chunk in enumerate(chunks):
        chunk_id = f"{shipment_id}_{doc_id}_chunk_{i}"
        existing = collection.get(ids=[chunk_id])
        if existing["ids"]:
            continue
        ids.append(chunk_id)
        documents.append(chunk)
        metadatas.append({
            "shipment_id": shipment_id,
            "doc_path":    doc_path,
            "chunk_index": i,
        })

    if ids:
        collection.add(ids=ids, documents=documents, metadatas=metadatas)
        print(f"[RAG] Indexed {len(ids)} chunks for shipment {shipment_id}")
    else:
        print(f"[RAG] All chunks already indexed for shipment {shipment_id}")

    return len(ids)


# ══════════════════════════════════════════════════════════════════════════════
# QUERY
# ══════════════════════════════════════════════════════════════════════════════

def query_document(question: str, shipment_id: str, n_results: int = 3) -> list[dict]:
    """Query ChromaDB for relevant chunks from a specific shipment."""
    collection = _get_collection()
    try:
        results = collection.query(
            query_texts=[question],
            n_results=n_results,
            where={"shipment_id": shipment_id},
        )
    except Exception as e:
        print(f"[RAG] Query failed: {e}")
        return []

    snippets = []
    if results["documents"]:
        for i, doc in enumerate(results["documents"][0]):
            snippets.append({
                "text":     doc,
                "distance": results["distances"][0][i] if results.get("distances") else None,
                "metadata": results["metadatas"][0][i] if results.get("metadatas") else {},
            })
    return snippets


def answer_with_rag(question: str, shipment_id: str) -> str:
    """
    Answer a question about a specific shipment's document using RAG + Groq.
    """
    snippets = query_document(question, shipment_id)
    if not snippets:
        return "No relevant content found in the document for this question."

    context = "\n\n---\n\n".join([s["text"] for s in snippets])

    from llm.client import get_llm
    llm = get_llm(vision=False)

    prompt = f"""You are a trade document assistant. Answer the question based ONLY on the document snippets below.
If the answer is not present in the snippets, say "Not found in document."

Question: {question}

Document snippets:
{context}

Answer concisely and quote the exact relevant text from the document."""

    try:
        response = llm.invoke(prompt)
        return response.content if hasattr(response, "content") else str(response)
    except Exception as e:
        return f"RAG answer failed: {e}"


# ══════════════════════════════════════════════════════════════════════════════
# CLEANUP
# ══════════════════════════════════════════════════════════════════════════════

def delete_shipment_chunks(shipment_id: str):
    """Remove all indexed chunks for a shipment."""
    collection = _get_collection()
    existing = collection.get(where={"shipment_id": shipment_id})
    if existing["ids"]:
        collection.delete(ids=existing["ids"])
        print(f"[RAG] Deleted {len(existing['ids'])} chunks for shipment {shipment_id}")