"""
Think9 Brain — Ingestion Agent
Reads all documents in data/, extracts metadata, builds a TF-IDF vector
index, and saves it to index.pkl.

Note: this uses scikit-learn TF-IDF instead of a downloaded embedding model
(e.g. sentence-transformers) so it runs with zero external network calls.
"""
import os
import re
import pickle
from sklearn.feature_extraction.text import TfidfVectorizer

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
INDEX_PATH = os.path.join(os.path.dirname(__file__), "index.pkl")


def parse_doc(filepath):
    """Extract simple metadata (Brand, Document Type, Date) + body text."""
    with open(filepath, "r", encoding="utf-8") as f:
        text = f.read()

    meta = {"brand": None, "doc_type": None, "date": None, "applies_to": None}
    lines = text.splitlines()
    body_start = 0
    for i, line in enumerate(lines):
        if line.startswith("Brand:"):
            meta["brand"] = line.split(":", 1)[1].strip()
        elif line.startswith("Document Type:"):
            meta["doc_type"] = line.split(":", 1)[1].strip()
        elif line.startswith("Last Updated:") or line.startswith("Date:"):
            meta["date"] = line.split(":", 1)[1].strip()
        elif line.startswith("Applies To:"):
            meta["applies_to"] = line.split(":", 1)[1].strip()
        elif line.strip() == "" and i > 0:
            body_start = i + 1
            break
    body = "\n".join(lines[body_start:]).strip()
    is_master_policy = meta["applies_to"] is not None or "Master" in os.path.basename(filepath) or "master" in filepath.lower()
    meta["authority_level"] = "group_policy" if is_master_policy else "brand_level"
    return meta, body


def chunk_text(text, max_words=80):
    """Simple paragraph-based chunking"""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks = []
    for p in paras:
        words = p.split()
        if len(words) <= max_words:
            chunks.append(p)
        else:
            for i in range(0, len(words), max_words):
                chunks.append(" ".join(words[i:i + max_words]))
    return chunks if chunks else [text]


def build_index():
    records = []  # one per chunk: {text, brand, doc_type, date, authority_level, source}
    for fname in sorted(os.listdir(DATA_DIR)):
        if not fname.endswith(".txt"):
            continue
        fpath = os.path.join(DATA_DIR, fname)
        meta, body = parse_doc(fpath)
        for chunk in chunk_text(body):
            records.append({
                "text": chunk,
                "brand": meta["brand"],
                "doc_type": meta["doc_type"],
                "date": meta["date"],
                "authority_level": meta["authority_level"],
                "source": fname,
            })

    corpus = [r["text"] for r in records]
    vectorizer = TfidfVectorizer(stop_words="english", ngram_range=(1, 2))
    matrix = vectorizer.fit_transform(corpus)

    with open(INDEX_PATH, "wb") as f:
        pickle.dump({"records": records, "vectorizer": vectorizer, "matrix": matrix}, f)

    print(f"Ingested {len(records)} chunks from {len(os.listdir(DATA_DIR))} documents -> {INDEX_PATH}")


if __name__ == "__main__":
    build_index()
