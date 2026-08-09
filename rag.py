"""
Think9 Brain — Retrieval, Contradiction/Verification, and Synthesis agents.

Design note: `synthesize_answer()` calls the Claude API if ANTHROPIC_API_KEY
is set in the environment (gives a natural-language, cited answer). If no
key is set, it falls back to a deterministic template so the whole pipeline
still runs end-to-end for demo purposes with zero external dependencies.
"""
import os
import re
import pickle
from sklearn.metrics.pairwise import cosine_similarity

INDEX_PATH = os.path.join(os.path.dirname(__file__), "index.pkl")

TOPIC_KEYWORDS = {
    "returns": ["return", "refund", "return window"],
    "vendor_payment": ["payment terms", "net 30", "net 45", "invoice", "supplier"],
}


def _load_index():
    with open(INDEX_PATH, "rb") as f:
        return pickle.load(f)


def retrieve(query, top_k=4):
    idx = _load_index()
    q_vec = idx["vectorizer"].transform([query])
    sims = cosine_similarity(q_vec, idx["matrix"])[0]
    ranked = sorted(zip(idx["records"], sims), key=lambda x: x[1], reverse=True)
    results = []
    for rec, score in ranked[:top_k]:
        if score <= 0:
            continue
        r = dict(rec)
        r["score"] = float(score)
        results.append(r)
    return results


def _extract_day_count(text):
    m = re.search(r"(\d+)[\s-]*day", text)
    if m:
        return int(m.group(1))
    m = re.search(r"net[\s-]*(\d+)", text, re.IGNORECASE)
    if m:
        return int(m.group(1))
    return None


def _topic_of(text):
    text_l = text.lower()
    for topic, kws in TOPIC_KEYWORDS.items():
        if any(kw in text_l for kw in kws):
            return topic
    return None


def detect_contradictions(chunks):
    """
    Groups retrieved chunks by topic, then compares brand-level chunks
    against each other and against any group/master policy chunk on the
    same topic. Flags numeric mismatches (e.g. return-window days,
    payment-term days) with a confidence + severity score.
    """
    by_topic = {}
    for c in chunks:
        topic = _topic_of(c["text"])
        if topic:
            by_topic.setdefault(topic, []).append(c)

    contradictions = []
    for topic, group in by_topic.items():
        master = [c for c in group if c["authority_level"] == "group_policy"]
        brand_level = [c for c in group if c["authority_level"] == "brand_level"]

        # Compare each brand-level doc's day-count against the master policy's day-count
        for m in master:
            m_days = _extract_day_count(m["text"])
            if m_days is None:
                continue
            for b in brand_level:
                b_days = _extract_day_count(b["text"])
                if b_days is None:
                    continue
                violates = (topic == "returns" and b_days < m_days) or \
                           (topic == "vendor_payment" and b_days > m_days)
                if violates:
                    contradictions.append({
                        "topic": topic,
                        "severity": "high",
                        "confidence": 0.9,
                        "detail": (
                            f"{b.get('brand', b['source'])} states {b_days} days, which conflicts "
                            f"with group policy ({m['doc_type']}) requiring {m_days} days."
                        ),
                        "sources": [b["source"], m["source"]],
                    })

        # Compare brand-level docs against each other even with no master policy present
        for i in range(len(brand_level)):
            for j in range(i + 1, len(brand_level)):
                a, b = brand_level[i], brand_level[j]
                a_days, b_days = _extract_day_count(a["text"]), _extract_day_count(b["text"])
                if a_days is not None and b_days is not None and a_days != b_days:
                    contradictions.append({
                        "topic": topic,
                        "severity": "medium",
                        "confidence": 0.75,
                        "detail": (
                            f"{a.get('brand', a['source'])} ({a_days} days) and "
                            f"{b.get('brand', b['source'])} ({b_days} days) differ on {topic.replace('_', ' ')} "
                            f"with no group policy reconciling them in the retrieved context."
                        ),
                        "sources": [a["source"], b["source"]],
                    })

    # de-duplicate identical flags
    seen = set()
    unique = []
    for c in contradictions:
        key = (c["topic"], tuple(sorted(c["sources"])))
        if key not in seen:
            seen.add(key)
            unique.append(c)
    return unique


def synthesize_answer(query, chunks, contradictions):
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key:
        return _synthesize_with_claude(query, chunks, contradictions, api_key)
    return _synthesize_template(query, chunks, contradictions)


def _synthesize_template(query, chunks, contradictions):
    if not chunks:
        return "No relevant documents found for this query."
    lines = [f"Based on {len(chunks)} retrieved document(s):"]
    for c in chunks:
        label = c.get("brand") or c["doc_type"]
        lines.append(f"- [{label} — {c['source']}] {c['text']}")
    if contradictions:
        lines.append("\n⚠ Contradiction(s) detected:")
        for ct in contradictions:
            lines.append(f"- ({ct['severity'].upper()}, confidence {ct['confidence']:.2f}) {ct['detail']}")
    return "\n".join(lines)


def _synthesize_with_claude(query, chunks, contradictions, api_key):
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    context = "\n\n".join(f"[Source: {c['source']}] {c['text']}" for c in chunks)
    conflict_note = ""
    if contradictions:
        conflict_note = "\n\nDetected contradictions:\n" + "\n".join(
            f"- {ct['detail']}" for ct in contradictions
        )
    prompt = (
        f"You are Think9 Brain, an internal knowledge assistant. Answer the "
        f"employee's question using ONLY the context below, and cite sources "
        f"by filename. If there is a contradiction, surface it clearly instead "
        f"of picking one version.\n\nContext:\n{context}{conflict_note}\n\n"
        f"Question: {query}\n\nAnswer:"
    )
    resp = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=400,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text


def answer_query(query, top_k=4):
    chunks = retrieve(query, top_k=top_k)
    contradictions = detect_contradictions(chunks)
    answer = synthesize_answer(query, chunks, contradictions)
    return {
        "query": query,
        "answer": answer,
        "sources": [{"source": c["source"], "brand": c.get("brand"), "score": round(c["score"], 3)} for c in chunks],
        "contradictions": contradictions,
    }


def scan_corpus():
    """
    Proactive agent: scans the ENTIRE ingested corpus for contradictions,
    independent of any single user query. This is what makes the system a
    continuously-monitoring "Brain" rather than a reactive Q&A bot — it's
    meant to run on a schedule (e.g. nightly, or on every new document
    ingested) and push flagged conflicts to reviewers proactively.
    """
    idx = _load_index()
    all_records = idx["records"]
    contradictions = detect_contradictions(all_records)

    docs_scanned = len({r["source"] for r in all_records})
    severity_counts = {"high": 0, "medium": 0}
    for c in contradictions:
        severity_counts[c["severity"]] = severity_counts.get(c["severity"], 0) + 1

    return {
        "docs_scanned": docs_scanned,
        "chunks_scanned": len(all_records),
        "contradictions_found": len(contradictions),
        "severity_counts": severity_counts,
        "contradictions": contradictions,
    }


if __name__ == "__main__":
    import json
    for q in [
        "What is BrandB's return policy and is it compliant?",
        "What are BrandC's vendor payment terms?",
    ]:
        print("Q:", q)
        print(json.dumps(answer_query(q), indent=2))
        print("-" * 60)
