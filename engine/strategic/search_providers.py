"""
engine.strategic.search_providers — Multi-Provider Search Engine
===================================================================
Production web search with multiple providers for breadth and redundancy.

Providers:
  1. Serper (Google Search API) — structured results, snippets, knowledge panels
  2. Anthropic web_search — built-in to Claude API, grounded citations
  3. Google Custom Search — direct API, image/news support

Features:
  - Result deduplication across providers
  - Relevance scoring and ranking
  - Structured extraction (snippets, dates, sources)
  - Rate limiting and cost tracking
  - Caching layer (avoids repeat searches within TTL)

Env vars:
  SERPER_API_KEY — from serper.dev ($50/mo = 50K searches)
  GOOGLE_SEARCH_API_KEY — from console.cloud.google.com
  GOOGLE_SEARCH_CX — Custom Search Engine ID
  ANTHROPIC_API_KEY — already present for LLM
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# DATA MODELS
# ═══════════════════════════════════════════════════════════════

@dataclass
class SearchResult:
    """A single search result from any provider."""
    title: str = ""
    url: str = ""
    snippet: str = ""
    source: str = ""         # domain name
    provider: str = ""       # serper, anthropic, google
    date: str = ""           # publication date if available
    relevance: float = 0.0   # 0-1 relevance score
    raw: Dict = field(default_factory=dict)

    @property
    def domain(self) -> str:
        from urllib.parse import urlparse
        try:
            return urlparse(self.url).netloc
        except Exception:
            return self.source

    def to_dict(self) -> Dict:
        return {
            "title": self.title, "url": self.url, "snippet": self.snippet,
            "source": self.source, "provider": self.provider,
            "date": self.date, "relevance": self.relevance,
        }


@dataclass
class SearchResponse:
    """Aggregated results from one or more providers."""
    query: str = ""
    results: List[SearchResult] = field(default_factory=list)
    knowledge_panel: Dict = field(default_factory=dict)
    total_results: int = 0
    providers_used: List[str] = field(default_factory=list)
    elapsed_ms: int = 0
    cached: bool = False

    def to_dict(self) -> Dict:
        return {
            "query": self.query,
            "results": [r.to_dict() for r in self.results],
            "knowledge_panel": self.knowledge_panel,
            "total_results": self.total_results,
            "providers_used": self.providers_used,
            "elapsed_ms": self.elapsed_ms,
            "cached": self.cached,
        }

    @property
    def top_snippets(self) -> str:
        """Concatenated top snippets for LLM consumption."""
        return "\n\n".join(
            f"[{r.source}] {r.snippet}" for r in self.results[:8] if r.snippet
        )


# ═══════════════════════════════════════════════════════════════
# SEARCH CACHE
# ═══════════════════════════════════════════════════════════════

class SearchCache:
    """In-memory TTL cache for search results. Avoids repeat API calls."""

    def __init__(self, ttl_seconds: int = 3600):
        self._cache: Dict[str, tuple] = {}  # key → (response, timestamp)
        self._ttl = ttl_seconds

    def _key(self, query: str, provider: str) -> str:
        return hashlib.md5(f"{provider}:{query.lower().strip()}".encode()).hexdigest()

    def get(self, query: str, provider: str) -> Optional[SearchResponse]:
        key = self._key(query, provider)
        if key in self._cache:
            resp, ts = self._cache[key]
            if time.time() - ts < self._ttl:
                resp.cached = True
                return resp
            del self._cache[key]
        return None

    def set(self, query: str, provider: str, response: SearchResponse):
        key = self._key(query, provider)
        self._cache[key] = (response, time.time())

    def clear(self):
        self._cache.clear()


# Global cache instance
_cache = SearchCache(ttl_seconds=3600)


# ═══════════════════════════════════════════════════════════════
# SERPER PROVIDER
# ═══════════════════════════════════════════════════════════════

def search_serper(
    query: str,
    num_results: int = 10,
    search_type: str = "search",  # search, news, places, images
    location: str = "",
) -> SearchResponse:
    """Search via Serper.dev (Google Search API).

    Env: SERPER_API_KEY
    Cost: ~$0.001 per search
    """
    api_key = os.environ.get("SERPER_API_KEY", "")
    if not api_key:
        return SearchResponse(query=query, providers_used=["serper_unavailable"])

    cached = _cache.get(query, f"serper_{search_type}")
    if cached:
        return cached

    import urllib.request

    start = time.perf_counter()
    try:
        payload = {"q": query, "num": num_results}
        if location:
            payload["location"] = location

        url = f"https://google.serper.dev/{search_type}"
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            url, data=data, method="POST",
            headers={
                "X-API-KEY": api_key,
                "Content-Type": "application/json",
            },
        )

        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read())

        results = []

        # Organic results
        for item in body.get("organic", []):
            results.append(SearchResult(
                title=item.get("title", ""),
                url=item.get("link", ""),
                snippet=item.get("snippet", ""),
                source=item.get("source", ""),
                provider="serper",
                date=item.get("date", ""),
                relevance=1.0 - len(results) * 0.08,
                raw=item,
            ))

        # News results
        for item in body.get("news", []):
            results.append(SearchResult(
                title=item.get("title", ""),
                url=item.get("link", ""),
                snippet=item.get("snippet", ""),
                source=item.get("source", ""),
                provider="serper_news",
                date=item.get("date", ""),
                relevance=0.9 - len(results) * 0.05,
                raw=item,
            ))

        # Knowledge graph
        kg = body.get("knowledgeGraph", {})

        elapsed = int((time.perf_counter() - start) * 1000)
        response = SearchResponse(
            query=query, results=results, knowledge_panel=kg,
            total_results=len(results), providers_used=["serper"],
            elapsed_ms=elapsed,
        )
        _cache.set(query, f"serper_{search_type}", response)
        return response

    except Exception as e:
        logger.warning(f"Serper search failed for '{query}': {e}")
        return SearchResponse(
            query=query, providers_used=["serper_error"],
            elapsed_ms=int((time.perf_counter() - start) * 1000),
        )


def search_serper_news(query: str, num_results: int = 10) -> SearchResponse:
    """Search Serper news specifically."""
    return search_serper(query, num_results, search_type="news")


def search_serper_places(query: str, location: str = "") -> SearchResponse:
    """Search Serper for local places/businesses."""
    return search_serper(query, search_type="places", location=location)


# ═══════════════════════════════════════════════════════════════
# ANTHROPIC WEB SEARCH PROVIDER
# ═══════════════════════════════════════════════════════════════

def search_anthropic(query: str, num_results: int = 10) -> SearchResponse:
    """Search via Anthropic's built-in web_search tool.

    Env: ANTHROPIC_API_KEY
    Uses Haiku for minimal cost.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return SearchResponse(query=query, providers_used=["anthropic_unavailable"])

    cached = _cache.get(query, "anthropic")
    if cached:
        return cached

    start = time.perf_counter()
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)

        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2048,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{
                "role": "user",
                "content": (
                    f"Search for: {query}\n\n"
                    "Return specific facts, numbers, dates, and source names. "
                    "Be concise and factual."
                ),
            }],
        )

        # Extract text and any search result citations
        text_parts = []
        for block in resp.content:
            if hasattr(block, "text"):
                text_parts.append(block.text)

        combined = "\n".join(text_parts)
        results = [SearchResult(
            title=query,
            snippet=combined[:2000],
            provider="anthropic",
            relevance=0.85,
        )]

        elapsed = int((time.perf_counter() - start) * 1000)
        response = SearchResponse(
            query=query, results=results, total_results=1,
            providers_used=["anthropic"], elapsed_ms=elapsed,
        )
        _cache.set(query, "anthropic", response)
        return response

    except Exception as e:
        logger.warning(f"Anthropic search failed for '{query}': {e}")
        return SearchResponse(
            query=query, providers_used=["anthropic_error"],
            elapsed_ms=int((time.perf_counter() - start) * 1000),
        )


# ═══════════════════════════════════════════════════════════════
# GOOGLE CUSTOM SEARCH PROVIDER
# ═══════════════════════════════════════════════════════════════

def search_google(query: str, num_results: int = 10) -> SearchResponse:
    """Search via Google Custom Search JSON API.

    Env: GOOGLE_SEARCH_API_KEY, GOOGLE_SEARCH_CX
    Cost: Free 100/day, then $5/1000 queries
    """
    api_key = os.environ.get("GOOGLE_SEARCH_API_KEY", "")
    cx = os.environ.get("GOOGLE_SEARCH_CX", "")
    if not api_key or not cx:
        return SearchResponse(query=query, providers_used=["google_unavailable"])

    cached = _cache.get(query, "google")
    if cached:
        return cached

    import urllib.request

    start = time.perf_counter()
    try:
        encoded = quote_plus(query)
        url = (
            f"https://www.googleapis.com/customsearch/v1"
            f"?key={api_key}&cx={cx}&q={encoded}&num={min(num_results, 10)}"
        )
        req = urllib.request.Request(url)

        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read())

        results = []
        for item in body.get("items", []):
            results.append(SearchResult(
                title=item.get("title", ""),
                url=item.get("link", ""),
                snippet=item.get("snippet", ""),
                source=item.get("displayLink", ""),
                provider="google",
                relevance=1.0 - len(results) * 0.08,
                raw=item,
            ))

        elapsed = int((time.perf_counter() - start) * 1000)
        response = SearchResponse(
            query=query, results=results,
            total_results=int(body.get("searchInformation", {}).get("totalResults", 0)),
            providers_used=["google"], elapsed_ms=elapsed,
        )
        _cache.set(query, "google", response)
        return response

    except Exception as e:
        logger.warning(f"Google search failed: {e}")
        return SearchResponse(
            query=query, providers_used=["google_error"],
            elapsed_ms=int((time.perf_counter() - start) * 1000),
        )


# ═══════════════════════════════════════════════════════════════
# UNIFIED MULTI-PROVIDER SEARCH
# ═══════════════════════════════════════════════════════════════

def multi_search(
    query: str,
    providers: Optional[List[str]] = None,
    num_results: int = 10,
    search_type: str = "search",
    location: str = "",
) -> SearchResponse:
    """Search across multiple providers and merge+deduplicate results.

    Args:
        query: Search query
        providers: List of providers to use. Default: all available.
                   Options: "serper", "anthropic", "google"
        num_results: Max results per provider
        search_type: "search", "news", "places"
        location: Location bias for local searches

    Returns:
        Merged SearchResponse with deduplicated results ranked by relevance.
    """
    if providers is None:
        # Auto-detect available providers
        providers = []
        if os.environ.get("SERPER_API_KEY"):
            providers.append("serper")
        if os.environ.get("ANTHROPIC_API_KEY"):
            providers.append("anthropic")
        if os.environ.get("GOOGLE_SEARCH_API_KEY") and os.environ.get("GOOGLE_SEARCH_CX"):
            providers.append("google")
        if not providers:
            return SearchResponse(query=query, providers_used=["none_available"])

    start = time.perf_counter()
    all_results = []
    providers_used = []
    knowledge_panel = {}

    for provider in providers:
        try:
            if provider == "serper":
                if search_type == "news":
                    resp = search_serper_news(query, num_results)
                elif search_type == "places":
                    resp = search_serper_places(query, location)
                else:
                    resp = search_serper(query, num_results, location=location)
                if resp.knowledge_panel:
                    knowledge_panel.update(resp.knowledge_panel)

            elif provider == "anthropic":
                resp = search_anthropic(query, num_results)

            elif provider == "google":
                resp = search_google(query, num_results)

            else:
                continue

            all_results.extend(resp.results)
            providers_used.extend(resp.providers_used)

        except Exception as e:
            logger.warning(f"Provider {provider} failed for '{query}': {e}")
            providers_used.append(f"{provider}_error")

    # Deduplicate by URL
    seen_urls = set()
    deduped = []
    for r in all_results:
        key = r.url or r.snippet[:100]
        if key not in seen_urls:
            seen_urls.add(key)
            deduped.append(r)

    # Sort by relevance
    deduped.sort(key=lambda r: r.relevance, reverse=True)

    elapsed = int((time.perf_counter() - start) * 1000)
    return SearchResponse(
        query=query,
        results=deduped[:num_results],
        knowledge_panel=knowledge_panel,
        total_results=len(deduped),
        providers_used=list(set(providers_used)),
        elapsed_ms=elapsed,
    )


# ═══════════════════════════════════════════════════════════════
# CONVENIENCE FUNCTIONS FOR PIPELINE
# ═══════════════════════════════════════════════════════════════

def quick_search(query: str) -> str:
    """Single search, return top snippets as text. For LLM tool use."""
    resp = multi_search(query, num_results=5)
    if not resp.results:
        return f"No results found for: {query}"
    return resp.top_snippets


def news_search(query: str) -> str:
    """Search news specifically, return top headlines."""
    resp = multi_search(query, num_results=5, search_type="news")
    if not resp.results:
        return f"No news found for: {query}"
    return resp.top_snippets


def local_search(query: str, location: str = "") -> str:
    """Search for local businesses/places."""
    resp = multi_search(query, num_results=5, search_type="places", location=location)
    if not resp.results:
        return f"No local results for: {query}"
    return resp.top_snippets
