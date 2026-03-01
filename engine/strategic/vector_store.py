"""
engine.strategic.vector_store — Market Intelligence Memory (Qdrant)
=====================================================================
Persistent vector database for storing and retrieving market research,
property comps, and trend data across all analyses.

Purpose:
  - Store every research finding so the system builds institutional knowledge
  - Find similar properties/markets from past analyses
  - Track market trends over time (gaming revenue, cap rates, demographics)
  - Avoid redundant searches — check memory first
  - Power "what similar deals have we analyzed?" queries

Collections:
  - market_research: Full research reports indexed by location + property type
  - property_comps: Individual comparable properties with financials
  - market_trends: Time-series market data points (NTI, cap rates, traffic)
  - construction_costs: Historical cost data from past construction projects

Env vars:
  QDRANT_URL — Qdrant instance URL (e.g. http://qdrant:6333)
  QDRANT_API_KEY — API key for Qdrant Cloud (optional for self-hosted)
  ANTHROPIC_API_KEY — for generating embeddings via Claude
"""

from __future__ import annotations

import json
import logging
import os
import time
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# EMBEDDING
# ═══════════════════════════════════════════════════════════════

def generate_embedding(text: str, model: str = "voyage-3") -> List[float]:
    """Generate text embedding for vector search.

    Uses Anthropic's Voyage embeddings (1024 dims) if available,
    falls back to a simple hash-based embedding for testing.
    """
    voyage_key = os.environ.get("VOYAGE_API_KEY", "")
    if voyage_key:
        try:
            import urllib.request
            payload = json.dumps({
                "input": [text[:8000]],  # Voyage max input
                "model": model,
            }).encode()
            req = urllib.request.Request(
                "https://api.voyageai.com/v1/embeddings",
                data=payload,
                method="POST",
                headers={
                    "Authorization": f"Bearer {voyage_key}",
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = json.loads(resp.read())
            return body["data"][0]["embedding"]
        except Exception as e:
            logger.warning(f"Voyage embedding failed: {e}")

    # Fallback: OpenAI embeddings
    openai_key = os.environ.get("OPENAI_API_KEY", "")
    if openai_key:
        try:
            import openai
            client = openai.OpenAI(api_key=openai_key)
            resp = client.embeddings.create(
                model="text-embedding-3-small",
                input=text[:8000],
            )
            return resp.data[0].embedding
        except Exception as e:
            logger.warning(f"OpenAI embedding failed: {e}")

    # Last resort: deterministic hash embedding (for testing only)
    return _hash_embedding(text, dims=1024)


def _hash_embedding(text: str, dims: int = 1024) -> List[float]:
    """Deterministic pseudo-embedding for testing. NOT for production."""
    import struct
    h = hashlib.sha512(text.encode()).digest()
    # Extend hash to fill dims
    raw = h * (dims // len(h) + 1)
    floats = []
    for i in range(dims):
        byte_val = raw[i]
        floats.append((byte_val / 255.0) * 2 - 1)  # Normalize to [-1, 1]
    return floats


# ═══════════════════════════════════════════════════════════════
# QDRANT CLIENT
# ═══════════════════════════════════════════════════════════════

class VectorStore:
    """Qdrant-backed vector store for market intelligence.

    Manages collections, upserts, and similarity search.
    Falls back to in-memory storage if Qdrant unavailable.
    """

    COLLECTIONS = {
        "market_research": {
            "size": 1024,
            "description": "Full market research reports by location",
        },
        "property_comps": {
            "size": 1024,
            "description": "Individual comparable property data",
        },
        "market_trends": {
            "size": 1024,
            "description": "Time-series market data points",
        },
        "construction_costs": {
            "size": 1024,
            "description": "Historical construction cost data",
        },
    }

    def __init__(self, workspace_id: str = ""):
        self._workspace_id = workspace_id
        self._client = None
        self._fallback: Dict[str, List[Dict]] = {}  # In-memory fallback
        self._init_client()

    def _init_client(self):
        """Connect to Qdrant if available."""
        url = os.environ.get("QDRANT_URL", "")
        api_key = os.environ.get("QDRANT_API_KEY", "")

        if not url:
            logger.info("VectorStore: No QDRANT_URL, using in-memory fallback")
            return

        try:
            from qdrant_client import QdrantClient
            from qdrant_client.models import Distance, VectorParams

            self._client = QdrantClient(
                url=url,
                api_key=api_key if api_key else None,
                timeout=10,
            )

            # Ensure collections exist
            existing = {c.name for c in self._client.get_collections().collections}
            for name, config in self.COLLECTIONS.items():
                col_name = f"{self._workspace_id}_{name}" if self._workspace_id else name
                if col_name not in existing:
                    self._client.create_collection(
                        collection_name=col_name,
                        vectors_config=VectorParams(
                            size=config["size"],
                            distance=Distance.COSINE,
                        ),
                    )
                    logger.info(f"Created Qdrant collection: {col_name}")

            logger.info(f"VectorStore: Connected to Qdrant at {url}")

        except Exception as e:
            logger.warning(f"VectorStore: Qdrant init failed ({e}), using fallback")
            self._client = None

    @property
    def is_connected(self) -> bool:
        return self._client is not None

    def _col_name(self, collection: str) -> str:
        return f"{self._workspace_id}_{collection}" if self._workspace_id else collection

    # ── Upsert ────────────────────────────────────────────

    def store(
        self,
        collection: str,
        doc_id: str,
        text: str,
        metadata: Dict[str, Any],
        embedding: Optional[List[float]] = None,
    ) -> bool:
        """Store a document with its embedding and metadata.

        Args:
            collection: One of COLLECTIONS keys
            doc_id: Unique document identifier
            text: Text content to embed and store
            metadata: Structured metadata (address, date, type, etc.)
            embedding: Pre-computed embedding, or None to generate
        """
        if embedding is None:
            embedding = generate_embedding(text)

        metadata["_text_preview"] = text[:500]
        metadata["_stored_at"] = datetime.now(timezone.utc).isoformat()
        metadata["_workspace"] = self._workspace_id

        if self._client:
            try:
                from qdrant_client.models import PointStruct
                self._client.upsert(
                    collection_name=self._col_name(collection),
                    points=[PointStruct(
                        id=_stable_int_id(doc_id),
                        vector=embedding,
                        payload=metadata,
                    )],
                )
                return True
            except Exception as e:
                logger.warning(f"Qdrant upsert failed: {e}")

        # Fallback
        if collection not in self._fallback:
            self._fallback[collection] = []
        self._fallback[collection].append({
            "id": doc_id, "embedding": embedding, "metadata": metadata,
        })
        return True

    # ── Search ────────────────────────────────────────────

    def search(
        self,
        collection: str,
        query_text: str,
        top_k: int = 5,
        filters: Optional[Dict] = None,
    ) -> List[Dict]:
        """Find similar documents by text query.

        Returns list of {id, score, metadata} dicts.
        """
        embedding = generate_embedding(query_text)

        if self._client:
            try:
                from qdrant_client.models import Filter, FieldCondition, MatchValue

                qdrant_filter = None
                if filters:
                    conditions = []
                    for key, value in filters.items():
                        conditions.append(
                            FieldCondition(key=key, match=MatchValue(value=value))
                        )
                    qdrant_filter = Filter(must=conditions)

                hits = self._client.search(
                    collection_name=self._col_name(collection),
                    query_vector=embedding,
                    limit=top_k,
                    query_filter=qdrant_filter,
                )

                return [
                    {"id": str(h.id), "score": h.score, "metadata": h.payload}
                    for h in hits
                ]
            except Exception as e:
                logger.warning(f"Qdrant search failed: {e}")

        # Fallback: brute-force cosine similarity
        docs = self._fallback.get(collection, [])
        if not docs:
            return []

        scored = []
        for doc in docs:
            score = _cosine_sim(embedding, doc["embedding"])
            if filters:
                if not all(doc["metadata"].get(k) == v for k, v in filters.items()):
                    continue
            scored.append({"id": doc["id"], "score": score, "metadata": doc["metadata"]})

        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:top_k]

    # ── Market Trend Storage ──────────────────────────────

    def store_trend_point(
        self,
        metric: str,
        value: float,
        location: str,
        state: str,
        date: Optional[str] = None,
        source: str = "",
    ):
        """Store a market trend data point for time-series tracking.

        Examples:
            store_trend_point("avg_nti", 1200.0, "Springfield", "IL")
            store_trend_point("cap_rate", 0.082, "Springfield", "IL")
            store_trend_point("gaming_locations", 2400, "Sangamon County", "IL")
        """
        date = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        doc_id = f"trend_{metric}_{location}_{date}"
        text = f"{metric}: {value} in {location}, {state} on {date}"
        metadata = {
            "metric": metric,
            "value": value,
            "location": location,
            "state": state,
            "date": date,
            "source": source,
            "type": "trend_point",
        }
        self.store("market_trends", doc_id, text, metadata)

    def get_trend_history(
        self, metric: str, location: str, state: str = "", top_k: int = 20,
    ) -> List[Dict]:
        """Retrieve trend history for a metric at a location."""
        query = f"{metric} {location} {state} trend history"
        results = self.search("market_trends", query, top_k=top_k, filters={
            "metric": metric,
        })
        return results

    # ── Research Memory ───────────────────────────────────

    def store_research(self, address: str, report: Dict):
        """Store a market research report for future retrieval."""
        text = json.dumps(report.get("executive_summary", ""), default=str)
        text += " " + address
        meta = report.get("_meta", {})
        metadata = {
            "address": address,
            "site_score": report.get("site_score", 0),
            "site_grade": report.get("site_grade", ""),
            "type": "research_report",
            "property_type": meta.get("property_type", ""),
            "state": meta.get("state", ""),
            "city": meta.get("city", ""),
        }
        doc_id = f"research_{hashlib.md5(address.encode()).hexdigest()[:12]}"
        self.store("market_research", doc_id, text, metadata)

    def find_similar_sites(self, address: str, top_k: int = 5) -> List[Dict]:
        """Find previously researched sites similar to this one."""
        return self.search("market_research", address, top_k=top_k)

    # ── Construction Cost Memory ──────────────────────────

    def store_construction_cost(
        self,
        project_name: str,
        project_type: str,
        total_cost: float,
        sqft: float,
        location: str,
        state: str,
        details: Dict = None,
    ):
        """Store historical construction cost for comp database."""
        cost_per_sqft = total_cost / sqft if sqft > 0 else 0
        text = (
            f"{project_type} construction in {location}, {state}: "
            f"${total_cost:,.0f} total, ${cost_per_sqft:,.0f}/sqft, {sqft:,.0f} sqft"
        )
        doc_id = f"construction_{hashlib.md5(project_name.encode()).hexdigest()[:12]}"
        metadata = {
            "project_name": project_name,
            "project_type": project_type,
            "total_cost": total_cost,
            "sqft": sqft,
            "cost_per_sqft": cost_per_sqft,
            "location": location,
            "state": state,
            "type": "construction_cost",
            **(details or {}),
        }
        self.store("construction_costs", doc_id, text, metadata)

    def find_similar_construction(
        self, project_type: str, location: str, top_k: int = 5,
    ) -> List[Dict]:
        """Find similar past construction projects for cost benchmarking."""
        query = f"{project_type} construction costs {location}"
        return self.search("construction_costs", query, top_k=top_k)


# ═══════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════

def _stable_int_id(doc_id: str) -> int:
    """Convert string ID to stable integer for Qdrant."""
    return int(hashlib.md5(doc_id.encode()).hexdigest()[:15], 16)


def _cosine_sim(a: List[float], b: List[float]) -> float:
    """Cosine similarity between two vectors."""
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
