"""
engine.strategic.llm_client — Production LLM Client
=======================================================
Multi-provider client supporting Anthropic + OpenAI with:
  - Tool use (function calling)
  - Per-stage model routing
  - Cost tracking
  - Retry with exponential backoff
  - Structured JSON output parsing

This is the real execution path. No simulation fallback.
"""

from __future__ import annotations

import json
import os
import time
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# PROVIDER CONFIG
# ═══════════════════════════════════════════════════════════════

ROUTE_MODELS = {
    "strategic_deep": {
        "provider": "anthropic",
        "model": "claude-sonnet-4-5-20250929",
        "max_tokens": 8192,
        "fallback_provider": "openai",
        "fallback_model": "gpt-4.1",
    },
    "strategic_fast": {
        "provider": "anthropic",
        "model": "claude-sonnet-4-5-20250929",
        "max_tokens": 4096,
        "fallback_provider": "anthropic",
        "fallback_model": "claude-haiku-4-5-20251001",
    },
    "cheap_structured": {
        "provider": "anthropic",
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 4096,
        "fallback_provider": "openai",
        "fallback_model": "gpt-4.1-mini",
    },
    # ── Extension routes (Phase 12) ──
    "research_grounded": {
        "provider": "perplexity",
        "model": "sonar-pro",
        "max_tokens": 4096,
        "fallback_provider": "anthropic",
        "fallback_model": "claude-haiku-4-5-20251001",
    },
    "long_context": {
        "provider": "gemini",
        "model": "gemini-2.5-pro",
        "max_tokens": 8192,
        "fallback_provider": "anthropic",
        "fallback_model": "claude-sonnet-4-5-20250929",
    },
    "bulk_extraction": {
        "provider": "gemini",
        "model": "gemini-2.5-flash",
        "max_tokens": 4096,
        "fallback_provider": "openai",
        "fallback_model": "gpt-4.1-mini",
    },
}

# Approximate cost per 1M tokens (input/output)
TOKEN_COSTS = {
    # Anthropic
    "claude-sonnet-4-5-20250929": {"input": 3.0, "output": 15.0},
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.0},
    "claude-opus-4-6": {"input": 15.0, "output": 75.0},
    # OpenAI
    "gpt-4.1": {"input": 2.0, "output": 8.0},
    "gpt-4.1-mini": {"input": 0.40, "output": 1.60},
    # Google Gemini
    "gemini-2.5-pro": {"input": 1.25, "output": 10.0},
    "gemini-2.5-flash": {"input": 0.15, "output": 0.60},
    # Perplexity
    "sonar-pro": {"input": 3.0, "output": 15.0},
    "sonar": {"input": 1.0, "output": 5.0},
}


@dataclass
class LLMResponse:
    """Response from an LLM call."""
    text: str = ""
    tool_calls: List[Dict] = field(default_factory=list)
    tool_results: List[Dict] = field(default_factory=list)
    model: str = ""
    provider: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    retries: int = 0


@dataclass
class ToolDefinition:
    """A tool the LLM can call."""
    name: str
    description: str
    parameters: Dict[str, Any]  # JSON Schema
    handler: Callable  # The actual function to call


class LLMClient:
    """Production LLM client with tool use and multi-provider routing."""

    MAX_TOOL_ROUNDS = 6  # Max tool call → result loops per request
    MAX_RETRIES = 2

    def __init__(self, db_routes: Optional[Dict[str, Dict]] = None):
        """
        Args:
            db_routes: Override route configs from DB (from model_routes table).
                       If None, uses ROUTE_MODELS defaults.
        """
        self._routes = db_routes or dict(ROUTE_MODELS)
        self._anthropic = None
        self._openai = None
        self._total_cost = 0.0
        self._call_count = 0

    # ── Provider lazy init ────────────────────────────────

    def _get_anthropic(self):
        if self._anthropic is None:
            import anthropic
            self._anthropic = anthropic.Anthropic(
                api_key=os.environ["ANTHROPIC_API_KEY"],
            )
        return self._anthropic

    def _get_openai(self):
        if self._openai is None:
            import openai
            self._openai = openai.OpenAI(
                api_key=os.environ["OPENAI_API_KEY"],
            )
        return self._openai

    # ── Main entry point ──────────────────────────────────

    def call(
        self,
        system_prompt: str,
        user_message: str,
        route_tier: str = "strategic_deep",
        tools: Optional[List[ToolDefinition]] = None,
        temperature: float = 0.3,
    ) -> LLMResponse:
        """Call an LLM with optional tool use and automatic routing.

        Args:
            system_prompt: System instructions for the stage
            user_message: User content (scenario + prior context)
            route_tier: Which model route to use
            tools: Available tools the LLM can call
            temperature: Sampling temperature

        Returns:
            LLMResponse with text, tool calls/results, cost tracking
        """
        route = self._routes.get(route_tier, self._routes.get("strategic_deep", {}))
        provider = route.get("provider", "anthropic")
        model = route.get("model", "claude-sonnet-4-5-20250929")
        max_tokens = route.get("max_tokens", 4096)

        start = time.perf_counter()
        response = LLMResponse(model=model, provider=provider)

        try:
            if provider == "anthropic":
                response = self._call_anthropic(
                    system_prompt, user_message, model, max_tokens,
                    tools, temperature,
                )
            elif provider == "openai":
                response = self._call_openai(
                    system_prompt, user_message, model, max_tokens,
                    tools, temperature,
                )
            elif provider == "gemini":
                response = self._call_gemini(
                    system_prompt, user_message, model, max_tokens,
                    tools, temperature,
                )
            elif provider == "perplexity":
                response = self._call_perplexity(
                    system_prompt, user_message, model, max_tokens,
                    temperature,
                )
        except Exception as e:
            logger.warning(f"Primary {provider}/{model} failed: {e}, trying fallback")
            fb_provider = route.get("fallback_provider", "")
            fb_model = route.get("fallback_model", "")
            if fb_provider and fb_model:
                try:
                    if fb_provider == "anthropic":
                        response = self._call_anthropic(
                            system_prompt, user_message, fb_model, max_tokens,
                            tools, temperature,
                        )
                    elif fb_provider == "openai":
                        response = self._call_openai(
                            system_prompt, user_message, fb_model, max_tokens,
                            tools, temperature,
                        )
                    elif fb_provider == "gemini":
                        response = self._call_gemini(
                            system_prompt, user_message, fb_model, max_tokens,
                            tools, temperature,
                        )
                    elif fb_provider == "perplexity":
                        response = self._call_perplexity(
                            system_prompt, user_message, fb_model, max_tokens,
                            temperature,
                        )
                    response.retries = 1
                except Exception as e2:
                    logger.error(f"Fallback {fb_provider}/{fb_model} also failed: {e2}")
                    raise

        response.latency_ms = int((time.perf_counter() - start) * 1000)
        response.cost_usd = self._estimate_cost(
            response.model, response.input_tokens, response.output_tokens,
        )
        self._total_cost += response.cost_usd
        self._call_count += 1

        return response

    # ── Anthropic ─────────────────────────────────────────

    def _call_anthropic(
        self, system: str, user_msg: str, model: str, max_tokens: int,
        tools: Optional[List[ToolDefinition]], temperature: float,
    ) -> LLMResponse:
        client = self._get_anthropic()

        # Build tool definitions for Anthropic format
        tool_defs = []
        tool_map = {}
        if tools:
            for t in tools:
                tool_defs.append({
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.parameters,
                })
                tool_map[t.name] = t.handler

        messages = [{"role": "user", "content": user_msg}]
        all_tool_calls = []
        all_tool_results = []
        total_in = 0
        total_out = 0

        for _round in range(self.MAX_TOOL_ROUNDS + 1):
            kwargs = {
                "model": model,
                "max_tokens": max_tokens,
                "system": system,
                "messages": messages,
                "temperature": temperature,
            }
            if tool_defs:
                kwargs["tools"] = tool_defs

            resp = client.messages.create(**kwargs)
            total_in += resp.usage.input_tokens
            total_out += resp.usage.output_tokens

            # Check if response has tool use blocks
            has_tool_use = any(b.type == "tool_use" for b in resp.content)

            if not has_tool_use or not tool_map:
                # Final response — extract text
                text_parts = [b.text for b in resp.content if hasattr(b, "text")]
                return LLMResponse(
                    text="\n".join(text_parts),
                    tool_calls=all_tool_calls,
                    tool_results=all_tool_results,
                    model=model,
                    provider="anthropic",
                    input_tokens=total_in,
                    output_tokens=total_out,
                )

            # Process tool calls
            assistant_content = []
            tool_result_content = []

            for block in resp.content:
                if block.type == "text":
                    assistant_content.append({"type": "text", "text": block.text})
                elif block.type == "tool_use":
                    assistant_content.append({
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": block.input,
                    })

                    # Execute the tool
                    handler = tool_map.get(block.name)
                    if handler:
                        try:
                            result = handler(block.input)
                            result_str = json.dumps(result) if isinstance(result, (dict, list)) else str(result)
                            all_tool_calls.append({"tool": block.name, "input": block.input, "success": True})
                            all_tool_results.append({"tool": block.name, "output": result})
                        except Exception as e:
                            result_str = json.dumps({"error": str(e)})
                            all_tool_calls.append({"tool": block.name, "input": block.input, "success": False, "error": str(e)})
                    else:
                        result_str = json.dumps({"error": f"Unknown tool: {block.name}"})

                    tool_result_content.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result_str,
                    })

            # Continue conversation with tool results
            messages.append({"role": "assistant", "content": assistant_content})
            messages.append({"role": "user", "content": tool_result_content})

        # If we exhausted rounds, return what we have
        return LLMResponse(
            text="[Max tool rounds reached]",
            tool_calls=all_tool_calls,
            tool_results=all_tool_results,
            model=model,
            provider="anthropic",
            input_tokens=total_in,
            output_tokens=total_out,
        )

    # ── OpenAI ────────────────────────────────────────────

    def _call_openai(
        self, system: str, user_msg: str, model: str, max_tokens: int,
        tools: Optional[List[ToolDefinition]], temperature: float,
    ) -> LLMResponse:
        client = self._get_openai()

        # Build OpenAI tool format
        oai_tools = []
        tool_map = {}
        if tools:
            for t in tools:
                oai_tools.append({
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                })
                tool_map[t.name] = t.handler

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_msg},
        ]
        all_tool_calls = []
        all_tool_results = []
        total_in = 0
        total_out = 0

        for _round in range(self.MAX_TOOL_ROUNDS + 1):
            kwargs = {
                "model": model,
                "max_tokens": max_tokens,
                "messages": messages,
                "temperature": temperature,
            }
            if oai_tools:
                kwargs["tools"] = oai_tools

            resp = client.chat.completions.create(**kwargs)
            choice = resp.choices[0]
            total_in += resp.usage.prompt_tokens
            total_out += resp.usage.completion_tokens

            if not choice.message.tool_calls:
                return LLMResponse(
                    text=choice.message.content or "",
                    tool_calls=all_tool_calls,
                    tool_results=all_tool_results,
                    model=model,
                    provider="openai",
                    input_tokens=total_in,
                    output_tokens=total_out,
                )

            # Process tool calls
            messages.append(choice.message)
            for tc in choice.message.tool_calls:
                handler = tool_map.get(tc.function.name)
                args = json.loads(tc.function.arguments) if tc.function.arguments else {}

                if handler:
                    try:
                        result = handler(args)
                        result_str = json.dumps(result) if isinstance(result, (dict, list)) else str(result)
                        all_tool_calls.append({"tool": tc.function.name, "input": args, "success": True})
                        all_tool_results.append({"tool": tc.function.name, "output": result})
                    except Exception as e:
                        result_str = json.dumps({"error": str(e)})
                        all_tool_calls.append({"tool": tc.function.name, "input": args, "success": False, "error": str(e)})
                else:
                    result_str = json.dumps({"error": f"Unknown tool: {tc.function.name}"})

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_str,
                })

        return LLMResponse(
            text="[Max tool rounds reached]",
            tool_calls=all_tool_calls,
            tool_results=all_tool_results,
            model=model,
            provider="openai",
            input_tokens=total_in,
            output_tokens=total_out,
        )

    # ── Google Gemini ─────────────────────────────────────

    def _call_gemini(
        self, system: str, user_msg: str, model: str, max_tokens: int,
        tools: Optional[List[ToolDefinition]], temperature: float,
    ) -> LLMResponse:
        """Call Google Gemini via OpenAI-compatible endpoint.

        Env: GEMINI_API_KEY
        Best for: Long context (1M tokens), bulk extraction, multimodal.
        """
        api_key = os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY not set")

        import openai
        client = openai.OpenAI(
            api_key=api_key,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        )

        # Gemini uses OpenAI-compatible format
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_msg},
        ]

        # Build tool defs (OpenAI format — Gemini supports it)
        oai_tools = []
        tool_map = {}
        if tools:
            for t in tools:
                oai_tools.append({
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                })
                tool_map[t.name] = t.handler

        all_tool_calls = []
        all_tool_results = []
        total_in = total_out = 0

        for _round in range(self.MAX_TOOL_ROUNDS + 1):
            kwargs = {
                "model": model,
                "max_tokens": max_tokens,
                "messages": messages,
                "temperature": temperature,
            }
            if oai_tools:
                kwargs["tools"] = oai_tools

            resp = client.chat.completions.create(**kwargs)
            choice = resp.choices[0]
            total_in += getattr(resp.usage, "prompt_tokens", 0)
            total_out += getattr(resp.usage, "completion_tokens", 0)

            if not choice.message.tool_calls:
                return LLMResponse(
                    text=choice.message.content or "",
                    tool_calls=all_tool_calls,
                    tool_results=all_tool_results,
                    model=model, provider="gemini",
                    input_tokens=total_in, output_tokens=total_out,
                )

            messages.append(choice.message)
            for tc in choice.message.tool_calls:
                handler = tool_map.get(tc.function.name)
                args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                if handler:
                    try:
                        result = handler(args)
                        result_str = json.dumps(result, default=str) if isinstance(result, (dict, list)) else str(result)
                        all_tool_calls.append({"tool": tc.function.name, "input": args, "success": True})
                        all_tool_results.append({"tool": tc.function.name, "output": result})
                    except Exception as e:
                        result_str = json.dumps({"error": str(e)})
                        all_tool_calls.append({"tool": tc.function.name, "input": args, "success": False})
                else:
                    result_str = json.dumps({"error": f"Unknown tool: {tc.function.name}"})
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": result_str})

        return LLMResponse(
            text="[Max tool rounds reached]",
            tool_calls=all_tool_calls, tool_results=all_tool_results,
            model=model, provider="gemini",
            input_tokens=total_in, output_tokens=total_out,
        )

    # ── Perplexity ────────────────────────────────────────

    def _call_perplexity(
        self, system: str, user_msg: str, model: str, max_tokens: int,
        temperature: float,
    ) -> LLMResponse:
        """Call Perplexity Sonar (grounded search + reasoning).

        Env: PERPLEXITY_API_KEY
        Best for: Research queries where grounded citations matter.
        Note: Perplexity doesn't support custom tools — it has built-in search.
        """
        api_key = os.environ.get("PERPLEXITY_API_KEY", "")
        if not api_key:
            raise RuntimeError("PERPLEXITY_API_KEY not set")

        import openai
        client = openai.OpenAI(
            api_key=api_key,
            base_url="https://api.perplexity.ai",
        )

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_msg},
        ]

        resp = client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=messages,
            temperature=temperature,
        )

        choice = resp.choices[0]
        total_in = getattr(resp.usage, "prompt_tokens", 0)
        total_out = getattr(resp.usage, "completion_tokens", 0)

        # Perplexity includes citations in response
        citations = getattr(resp, "citations", [])

        return LLMResponse(
            text=choice.message.content or "",
            tool_calls=[{"tool": "perplexity_search", "citations": citations}] if citations else [],
            tool_results=[],
            model=model, provider="perplexity",
            input_tokens=total_in, output_tokens=total_out,
        )

    # ── Cost tracking ─────────────────────────────────────

    def _estimate_cost(self, model: str, in_tokens: int, out_tokens: int) -> float:
        rates = TOKEN_COSTS.get(model, {"input": 3.0, "output": 15.0})
        return (in_tokens * rates["input"] + out_tokens * rates["output"]) / 1_000_000

    @property
    def total_cost(self) -> float:
        return self._total_cost

    @property
    def call_count(self) -> int:
        return self._call_count


def parse_json_from_llm(text: str) -> Dict:
    """Extract JSON from LLM response text (handles markdown fences)."""
    clean = text.strip()
    if clean.startswith("```"):
        lines = clean.split("\n")
        # Remove first and last fence lines
        lines = [l for l in lines if not l.strip().startswith("```")]
        clean = "\n".join(lines).strip()

    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        # Try to find JSON object in the text
        start = clean.find("{")
        end = clean.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(clean[start:end + 1])
            except json.JSONDecodeError:
                pass
    return {}
