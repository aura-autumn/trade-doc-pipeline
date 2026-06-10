"""
RAG Layer
- Embeds trade documents into ChromaDB
- Answers "where in the doc" questions
- Used for source snippet retrieval during validation discrepancy review
"""

import os
import hashlib
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

CHROMA_PATH = os.getenv("CHROMA_PATH", "./data/chroma")
CHUNK_SIZE = 500
CHUNK_OVERLAP = 100


def _get_chroma_client():
    import chromadb
    os.makedirs(CHROMA_PATH, exist_ok=True)
    return chromadb.PersistentClient(path=CHROMA_PATH)


def _get_embedding_function():
    from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
    return SentenceTransformerEmbeddingFunction(model_name="all-MiniLM-L6-v2")


def _get_collection():
    client = _get_chroma_client()
    ef = _get_embedding_function()
    return client.get_or_create_collection(
        name="trade_documents",
        embedding_function=ef,
        metadata={"hnsw:space": "cosine"}
    )


def _extract_text_from_doc(doc_path: str) -> str:
    """Extract raw text from PDF or image for chunking."""
    suffix = Path(doc_path).suffix.lower()

    if suffix == ".pdf":
        try:
            import PyPDF2
            text = ""
            with open(doc_path, "rb") as f:
                reader = PyPDF2.PdfReader(f)
                for page in reader.pages:
                    text += page.extract_text() + "\n"
            if text.strip():
                return text
        except Exception:
            pass

        # Fallback: docling
        try:
            from docling.document_converter import DocumentConverter
            converter = DocumentConverter()
            result = converter.convert(doc_path)
            return result.document.export_to_markdown()
        except Exception as e:
            print(f"Text extraction failed: {e}")
            return ""

    elif suffix in (".jpg", ".jpeg", ".png", ".webp"):
        # For images, use LLM to get text description
        try:
            from llm.client import get_llm, build_vision_message
            llm = get_llm(vision=True)
            messages = build_vision_message(
                doc_path,
                "Extract all text from this trade document exactly as it appears. Include all fields, values, and numbers."
            )
            response = llm.invoke(messages)
            return response.content if hasattr(response, "content") else str(response)
        except Exception as e:
            print(f"Image text extraction failed: {e}")
            return ""

    return ""


def _chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split text into overlapping chunks."""
    words = text.split()
    chunks = []
    i = 0
    while i < len(words):
        chunk = " ".join(words[i:i + chunk_size])
        chunks.append(chunk)
        i += chunk_size - overlap
    return chunks


def index_document(doc_path: str, shipment_id: str) -> int:
    """
    Index a document into ChromaDB.
    Returns number of chunks indexed.
    """
    text = _extract_text_from_doc(doc_path)
    if not text.strip():
        print(f"No text extracted from {doc_path}")
        return 0

    chunks = _chunk_text(text)
    collection = _get_collection()

    doc_id = hashlib.md5(doc_path.encode()).hexdigest()[:8]

    ids = []
    documents = []
    metadatas = []

    for i, chunk in enumerate(chunks):
        chunk_id = f"{shipment_id}_{doc_id}_chunk_{i}"
        # Skip if already indexed
        existing = collection.get(ids=[chunk_id])
        if existing["ids"]:
            continue
        ids.append(chunk_id)
        documents.append(chunk)
        metadatas.append({
            "shipment_id": shipment_id,
            "doc_path": doc_path,
            "chunk_index": i,
        })

    if ids:
        collection.add(ids=ids, documents=documents, metadatas=metadatas)
        print(f"Indexed {len(ids)} chunks for shipment {shipment_id}")

    return len(ids)


def query_document(question: str, shipment_id: str, n_results: int = 3) -> list[dict]:
    """
    Query documents for a specific shipment.
    Returns relevant snippets with metadata.
    """
    collection = _get_collection()

    try:
        results = collection.query(
            query_texts=[question],
            n_results=n_results,
            where={"shipment_id": shipment_id},
        )
    except Exception as e:
        print(f"RAG query failed: {e}")
        return []

    snippets = []
    if results["documents"]:
        for i, doc in enumerate(results["documents"][0]):
            snippets.append({
                "text": doc,
                "distance": results["distances"][0][i] if results.get("distances") else None,
                "metadata": results["metadatas"][0][i] if results.get("metadatas") else {},
            })

    return snippets


def answer_with_rag(question: str, shipment_id: str) -> str:
    """
    Use RAG to answer a question about a specific document.
    Used for "show me the source snippet" queries.
    """
    snippets = query_document(question, shipment_id)
    if not snippets:
        return "No relevant content found in the document for this question."

    context = "\n\n---\n\n".join([s["text"] for s in snippets])

    from llm.client import get_llm
    llm = get_llm(vision=False)

    prompt = f"""You are a trade document assistant. Answer the question based ONLY on the provided document snippets.
If the answer is not in the snippets, say "Not found in document."

Question: {question}

Document snippets:
{context}

Answer concisely and quote the relevant part of the document."""

    try:
        response = llm.invoke(prompt)
        return response.content if hasattr(response, "content") else str(response)
    except Exception as e:
        return f"RAG answer failed: {e}"


def delete_shipment_chunks(shipment_id: str):
    """Remove all chunks for a shipment from the index."""
    collection = _get_collection()
    existing = collection.get(where={"shipment_id": shipment_id})
    if existing["ids"]:
        collection.delete(ids=existing["ids"])
        print(f"Deleted {len(existing['ids'])} chunks for shipment {shipment_id}")
