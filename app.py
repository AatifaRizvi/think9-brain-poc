"""
Think9 Brain — API layer.
Run: uvicorn app:app --reload --port 8000
Then open http://localhost:8000
"""
import uuid
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from rag import answer_query, scan_corpus

app = FastAPI(title="Think9 Brain")

# in-memory HITL review store
REVIEW_QUEUE = {}


class QueryRequest(BaseModel):
    query: str


class ReviewDecision(BaseModel):
    review_id: str
    decision: str  # "approve" | "reject"


@app.post("/query")
def query(req: QueryRequest):
    result = answer_query(req.query)
    for c in result["contradictions"]:
        review_id = str(uuid.uuid4())[:8]
        c["review_id"] = review_id
        REVIEW_QUEUE[review_id] = {**c, "status": "pending", "query": req.query}
    return result


@app.get("/scan-all")
def scan_all():
    """Proactive agent: scans the whole corpus for contradictions, no query needed."""
    result = scan_corpus()
    for c in result["contradictions"]:
        review_id = str(uuid.uuid4())[:8]
        c["review_id"] = review_id
        REVIEW_QUEUE[review_id] = {**c, "status": "pending", "query": "[proactive scan]"}
    return result


@app.post("/flag-review")
def flag_review(dec: ReviewDecision):
    item = REVIEW_QUEUE.get(dec.review_id)
    if not item:
        return {"error": "review_id not found"}
    item["status"] = "approved" if dec.decision == "approve" else "rejected"
    return {"review_id": dec.review_id, "status": item["status"]}


@app.get("/review-queue")
def get_review_queue():
    return list(REVIEW_QUEUE.values())


app.mount("/", StaticFiles(directory="static", html=True), name="static")
