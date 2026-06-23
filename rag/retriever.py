"""
RAG Layer — FAISS + LSA embeddings (sklearn TF-IDF + SVD).

Replaces ChromaDB entirely. No onnxruntime. No sentence-transformers.
No internet. Only faiss-cpu and scikit-learn, both already installed.

Embedder: TF-IDF (10k features, bigrams) -> TruncatedSVD (128-dim) -> L2-normalised.
One _ShipmentStore per shipment, fitted on that doc's own chunks so query vectors
live in the same space as indexed vectors.

Persistence: each store is pickled to ./data/rag_store/<hash>.pkl — one file per shipment.
"""

from __future__ import annotations
import os
import pickle
import hashlib
from pathlib import Path
from dotenv import load_dotenv

import warnings
import faiss
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.decomposition import TruncatedSVD
from sklearn.preprocessing import normalize

from core.logging_config import get_logger
from core.paths import resolve_data_path

load_dotenv()

log = get_logger(__name__)

# Anchor to the project root (see core/paths) so the RAG store is shared across
# every entry point, not tied to the launch directory.
RAG_STORE     = resolve_data_path(os.getenv("RAG_STORE_PATH", "./data/rag_store"))
CHUNK_SIZE    = int(os.getenv("RAG_CHUNK_SIZE", 500))
CHUNK_OVERLAP = int(os.getenv("RAG_CHUNK_OVERLAP", 100))
EMB_DIM       = 128   # SVD components — enough for short trade docs, fast to fit


# ══════════════════════════════════════════════════════════════════════════════
# PER-SHIPMENT STORE
# ══════════════════════════════════════════════════════════════════════════════

class _ShipmentStore:
    """Self-contained FAISS vector store for one shipment's document chunks."""

    def __init__(self):
        self.chunks: list[str] = []
        self.tfidf   = TfidfVectorizer(max_features=10000, ngram_range=(1, 2),
                                        sublinear_tf=True, min_df=1)
        self.svd     = TruncatedSVD(n_components=EMB_DIM, random_state=42)
        self.index   = None
        self._fitted = False

    def _fit_embed(self, texts: list[str]) -> np.ndarray:
        matrix = self.tfidf.fit_transform(texts)
        n_comp = min(EMB_DIM, matrix.shape[0] - 1, matrix.shape[1] - 1)
        if n_comp < 1:
            n_comp = 1
        self.svd.set_params(n_components=n_comp)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            vecs = self.svd.fit_transform(matrix).astype(np.float32)
        self._fitted = True
        return normalize(vecs, norm="l2")

    def _embed(self, texts: list[str]) -> np.ndarray:
        matrix = self.tfidf.transform(texts)
        vecs   = self.svd.transform(matrix).astype(np.float32)
        return normalize(vecs, norm="l2")

    def build(self, chunks: list[str]) -> int:
        if not chunks:
            return 0
        self.chunks    = chunks
        embeddings     = self._fit_embed(chunks)
        dim            = embeddings.shape[1]
        self.index     = faiss.IndexFlatIP(dim)
        faiss.normalize_L2(embeddings)
        self.index.add(embeddings)
        return self.index.ntotal

    def query(self, question: str, top_k: int = 3) -> list[dict]:
        if not self._fitted or self.index is None or self.index.ntotal == 0:
            return []
        q_vec = self._embed([question]).astype(np.float32)
        faiss.normalize_L2(q_vec)
        k = min(top_k, self.index.ntotal)
        scores, indices = self.index.search(q_vec, k)
        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx != -1:
                results.append({"text": self.chunks[idx], "score": float(score)})
        return results


# ══════════════════════════════════════════════════════════════════════════════
# PERSISTENCE
# ══════════════════════════════════════════════════════════════════════════════

def _store_path(shipment_id: str) -> Path:
    os.makedirs(RAG_STORE, exist_ok=True)
    safe = hashlib.md5(shipment_id.encode()).hexdigest()[:12]
    return Path(RAG_STORE) / f"{safe}.pkl"


def _save(shipment_id: str, store: _ShipmentStore) -> None:
    with open(_store_path(shipment_id), "wb") as f:
        pickle.dump(store, f)


def _load(shipment_id: str) -> _ShipmentStore | None:
    p = _store_path(shipment_id)
    if not p.exists():
        return None
    try:
        with open(p, "rb") as f:
            return pickle.load(f)
    except Exception as e:
        log.warning("Failed to load RAG store for shipment %s: %s", shipment_id, e)
        return None


# ══════════════════════════════════════════════════════════════════════════════
# TEXT EXTRACTION + CHUNKING
# ══════════════════════════════════════════════════════════════════════════════

def _extract_text(doc_path: str) -> str:
    try:
        from agents.extractor import extract_text
        text, method = extract_text(doc_path)
        log.info("RAG text extracted via %s (%d chars) from %s", method, len(text), doc_path)
        return text
    except Exception as e:
        log.warning("RAG text extraction failed for %s: %s", doc_path, e)
        return ""


def _chunk_text(text: str) -> list[str]:
    words = text.split()
    chunks, i = [], 0
    while i < len(words):
        chunks.append(" ".join(words[i:i + CHUNK_SIZE]))
        i += CHUNK_SIZE - CHUNK_OVERLAP
    return [c for c in chunks if c.strip()]


# ══════════════════════════════════════════════════════════════════════════════
# PUBLIC API  (same signatures as the old ChromaDB retriever)
# ══════════════════════════════════════════════════════════════════════════════

def index_documents(doc_paths: list[str], shipment_id: str) -> int:
    """
    Extract, chunk, embed, and persist ALL documents of one shipment as a single
    store. This is the multi-doc-correct entry point: a shipment is one BOL +
    Invoice + Packing List, and RAG must be able to answer over every page of
    every attachment — not just the first one.

    The store is (re)built from the union of all docs' chunks so the TF-IDF/SVD
    space is fitted over the whole shipment. Idempotent re-indexing is fine.

    Returns the total number of chunks indexed.
    """
    all_chunks: list[str] = []
    for doc_path in doc_paths:
        text = _extract_text(doc_path)
        if not text.strip():
            log.warning("No text extracted from %s — skipping for RAG", doc_path)
            continue
        all_chunks.extend(_chunk_text(text))

    if not all_chunks:
        log.warning("No chunks produced for shipment %s — nothing to index", shipment_id)
        return 0

    store = _ShipmentStore()
    n = store.build(all_chunks)
    _save(shipment_id, store)
    log.info("Indexed %d chunks from %d doc(s) for shipment %s", n, len(doc_paths), shipment_id)
    return n


def index_document(doc_path: str, shipment_id: str) -> int:
    """
    Single-document convenience wrapper. Kept for backward compatibility with
    Part 1 callers. Delegates to `index_documents`.
    """
    return index_documents([doc_path], shipment_id)


def query_document(question: str, shipment_id: str, n_results: int = 3) -> list[dict]:
    """Return relevant chunks from a shipment's indexed document(s)."""
    store = _load(shipment_id)
    if store is None:
        log.info("No RAG index found for shipment %s", shipment_id)
        return []
    return store.query(question, top_k=n_results)


def answer_with_rag(question: str, shipment_id: str) -> str:
    """Answer a question about a shipment's document using RAG + LLM."""
    snippets = query_document(question, shipment_id)
    if not snippets:
        return "No relevant content found in the document for this question."

    context = "\n\n---\n\n".join(s["text"] for s in snippets)

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


def delete_shipment_chunks(shipment_id: str) -> None:
    """Remove the persisted index for a shipment."""
    p = _store_path(shipment_id)
    if p.exists():
        p.unlink()
        log.info("Deleted RAG index for shipment %s", shipment_id)