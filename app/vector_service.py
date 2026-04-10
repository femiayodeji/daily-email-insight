import chromadb
from chromadb.config import Settings
from typing import List
import hashlib
import threading


client = chromadb.Client(Settings(
    anonymized_telemetry=False,
    allow_reset=True
))

collection = client.get_or_create_collection(
    name="emails",
    metadata={"hnsw:space": "cosine"}
)


_embed_lock = threading.Lock()
_last_fingerprint = ""


def _fingerprint_emails(email_texts: List[str]) -> str:
    hasher = hashlib.sha256()
    for email in email_texts:
        hasher.update(email.encode("utf-8", errors="ignore"))
        hasher.update(b"\n---\n")
    return hasher.hexdigest()


def embed_and_store_emails(email_texts: List[str]) -> bool:
    global _last_fingerprint

    if not email_texts:
        return False

    fingerprint = _fingerprint_emails(email_texts)

    with _embed_lock:
        if _last_fingerprint == fingerprint:
            return False

        existing = collection.get()
        if existing['ids']:
            collection.delete(ids=existing['ids'])

        documents = []
        metadatas = []
        ids = []

        for idx, email_text in enumerate(email_texts):
            documents.append(email_text)
            metadatas.append({"index": idx})
            ids.append(f"email_{idx}")

        if documents:
            collection.add(
                documents=documents,
                metadatas=metadatas,
                ids=ids
            )
            _last_fingerprint = fingerprint
            return True

        return False


def query_similar_emails(query_text: str, top_k: int = 5) -> List[str]:
    count = collection.count()
    if count == 0:
        return []
    
    results = collection.query(
        query_texts=[query_text],
        n_results=min(top_k, count)
    )
    
    if results and results['documents']:
        return results['documents'][0]
    return []


def get_all_stored_emails() -> List[str]:
    count = collection.count()
    if count == 0:
        return []
    
    results = collection.get()
    if not results:
        return []

    documents = results.get('documents') or []
    return [doc for doc in documents if isinstance(doc, str)]
