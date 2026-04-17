"""query endpoint — multi-turn chat with intent routing.

implements a 4-category SKR-inspired router (Wang et al., 2023; Adaptive-RAG,
Jeong et al., 2024) that classifies each query before deciding whether to
search the wiki (RETRIEVE), answer from parametric knowledge (DIRECT), or
combine both (HYBRID). a single cheap classification prompt routes the full
generation call.

source attribution is always returned so the UI can show where the answer
came from: "from your documents", "from the model", or "from both".

the /chat endpoint supports multi-turn conversations: the frontend sends
conversation history and the backend includes recent turns as context for
coherent follow-up answers.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import date, datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from web.api.services import LLAMA_URL, WIKI_DIR, WikiSearch, check_server_health

router = APIRouter()
logger = logging.getLogger(__name__)


def _ground_truths() -> str:
    """dynamic ground truths block injected into every generation prompt."""
    today = date.today()
    return (
        f"Ground truths (always accurate):\n"
        f"- Today's date: {today.strftime('%A, %B %d, %Y')} ({today.isoformat()})\n"
        f"- Current year: {today.year}\n"
        f"- This is a personal knowledge wiki maintained by the user\n"
        f"- All wiki content was extracted from the user's own documents\n"
        f"- The local LLM is Gemma 4 26B running on-device via llama.cpp\n"
        f"- No cloud API calls are made — everything runs locally\n\n"
    )


# --- request models. ---

class HistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=50_000)


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=5000)
    history: list[HistoryMessage] = Field(default_factory=list, max_length=20)
    save: bool = False
    reasoning: bool = True
    web_search: bool = False
    web_results_count: int = Field(default=10, ge=1, le=30)
    search_engine: Literal["duckduckgo", "google", "bing"] = "duckduckgo"


# --- intent classification. ---

_ROUTE_SYSTEM = (
    "You are a query router for a personal knowledge wiki. "
    "Classify the user's question into exactly one category.\n\n"
    "RETRIEVE — The question asks about specific people, organizations, papers, "
    "tools, events, claims, or facts that likely exist in the user's personal wiki. "
    "Needs wiki search.\n\n"
    "DIRECT — The question is general knowledge, trivia (weather, time, simple "
    "lookups), definitions, reasoning, math, code, creative writing, casual "
    "conversation, or anything that does NOT require searching the user's "
    "personal documents. The model's own knowledge suffices.\n\n"
    "HYBRID — The question touches both wiki-specific content AND general "
    "knowledge. Search the wiki but also allow the model to supplement "
    "with background context.\n\n"
    "REFUSE — The question is harmful, nonsensical, or completely out of scope.\n\n"
    "When in doubt between DIRECT and RETRIEVE, choose DIRECT — avoid "
    "unnecessary wiki searches for questions the model can answer alone.\n\n"
    "Respond with ONLY the category label in uppercase. Nothing else."
)

# bm25 score threshold below which retrieval results are considered noise.
_BM25_NOISE_THRESHOLD = 0.005

# history budget: how much context to allocate to conversation history.
# with 32k tokens per slot (~128k chars) and generation up to 8k tokens
# (~32k chars), we budget 24k chars for history. messages are included
# most-recent-first until the budget is exhausted.
_HISTORY_CHAR_BUDGET = 24_000


# how much history to feed the classifier. the goal is to resolve anaphora
# and pronoun references in short follow-up questions (e.g. "now?", "who?",
# "why?") without blowing the classifier's tiny 200-token budget. we keep
# only the last 2 turns and truncate each entry.
_ROUTE_HISTORY_TURNS = 2
_ROUTE_HISTORY_USER_MAX = 400
_ROUTE_HISTORY_ASSISTANT_MAX = 600


def _build_route_context(history: list[dict] | None) -> str:
    """compact history block for the intent classifier.

    only the last `_ROUTE_HISTORY_TURNS` turns are included, with each
    message tightly truncated, so short follow-up questions like "now?"
    can be routed correctly without burning the classifier's small token
    budget. returns an empty string when there is no usable history.
    """
    if not history:
        return ""

    selected: list[str] = []
    for msg in reversed(history):
        role = msg.get("role")
        content = (msg.get("content") or "").strip()
        if not content:
            continue
        if role == "user":
            label = "User"
            cap = _ROUTE_HISTORY_USER_MAX
        elif role == "assistant":
            label = "Assistant"
            cap = _ROUTE_HISTORY_ASSISTANT_MAX
        else:
            continue
        if len(content) > cap:
            content = content[:cap].rstrip() + "…"
        selected.append(f"{label}: {content}")
        if len(selected) >= _ROUTE_HISTORY_TURNS * 2:
            break

    if not selected:
        return ""

    selected.reverse()
    return "Recent conversation (for context only):\n" + "\n".join(selected) + "\n\n"


def _classify_intent(question: str, history: list[dict] | None = None) -> str:
    """classify a question into RETRIEVE, DIRECT, HYBRID, or REFUSE.

    uses a single short llm call for classification. max_tokens=200 to
    accommodate gemma 4's thinking overhead (~100-150 tokens) plus the label.

    when `history` is provided, a compact snippet of the last turns is
    prepended to the user message so anaphoric follow-ups like "now?" or
    "who are they?" are routed based on the real topic of the conversation
    rather than the surface form of the short question.

    strip_thinking=False because gemma 4 often places the classification
    label inside its <think> block (e.g. "let me analyze... DIRECT").
    we search the full raw response for the label.

    falls back to RETRIEVE on parse failure.
    """
    from llm_client import llm

    context_block = _build_route_context(history)
    if context_block:
        user_prompt = (
            f"{context_block}"
            f"Classify THIS new question (use the conversation above only to "
            f"resolve what the question is about):\n{question}"
        )
    else:
        user_prompt = question

    try:
        raw = llm(
            user_prompt,
            system=_ROUTE_SYSTEM,
            max_tokens=200,
            temperature=0.0,
            timeout=45,
            strip_thinking=False,
        )
        response = raw.strip().upper()

        for label in ("RETRIEVE", "DIRECT", "HYBRID", "REFUSE"):
            if label in response:
                logger.warning(
                    "intent route: %s for %r (history_turns=%d)",
                    label, question[:80], len(history or []),
                )
                return label

        logger.warning(
            "intent route: no label found (raw=%r), fallback RETRIEVE for %r",
            raw[:200], question[:80],
        )
        return "RETRIEVE"
    except Exception:
        logger.exception(
            "intent classification failed for %r, fallback RETRIEVE",
            question[:80],
        )
        return "RETRIEVE"


# --- conversation context. ---

def _build_history_block(history: list[dict]) -> str:
    """format conversation history as a text block for the prompt.

    fills from most-recent backwards until _HISTORY_CHAR_BUDGET is
    exhausted, so the model always sees the freshest context. long
    assistant answers are truncated individually to 2000 chars; user
    messages are kept in full (they're shorter and more important for
    context). returns empty string when no history is provided.
    """
    if not history:
        return ""

    budget = _HISTORY_CHAR_BUDGET
    selected: list[str] = []

    # walk backwards (most recent first).
    for msg in reversed(history):
        role = "User" if msg.get("role") == "user" else "Assistant"
        content = msg.get("content", "")
        # truncate long assistant answers more aggressively.
        max_msg = 2000 if role == "Assistant" else 4000
        if len(content) > max_msg:
            content = content[:max_msg] + "…"
        entry = f"{role}: {content}"
        if len(entry) > budget:
            # if nothing selected yet, include a truncated version.
            if not selected:
                selected.append(entry[:budget])
            break
        budget -= len(entry)
        selected.append(entry)

    if not selected:
        return ""

    # reverse back to chronological order.
    selected.reverse()
    return "Previous conversation:\n" + "\n\n".join(selected) + "\n\n---\n\n"


# --- retrieval. ---

def _retrieve_context(question: str) -> tuple[dict[str, str], list[str], float, float]:
    """search wiki for relevant pages.

    returns (context, names, elapsed_ms, top_score).
    """
    t0 = time.time()
    with WikiSearch() as ws:
        ranked = ws.search(question, top_k=20)
        top_score = ranked[0][1] if ranked else 0.0
        names = [name for name, *_ in ranked]
        context = ws.get_context(names) if ranked else {}
    elapsed = (time.time() - t0) * 1000
    return context, names, elapsed, top_score


# --- web search. ---

def _get_ddgs_class():
    """import DDGS from whichever package name is available.

    the ddgs package (new name) is preferred — duckduckgo-search v8.x
    is unreliable and returns 0 results for common queries.
    """
    try:
        from ddgs import DDGS
        return DDGS
    except ImportError:
        pass
    try:
        from duckduckgo_search import DDGS
        return DDGS
    except ImportError:
        return None


def _search_duckduckgo(question: str, max_results: int = 10) -> list[dict[str, str]]:
    """search via DuckDuckGo."""
    DDGS = _get_ddgs_class()
    if DDGS is None:
        logger.warning("duckduckgo unavailable: pip install duckduckgo-search")
        return []
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(question, max_results=max_results))
        return [{"title": r.get("title", ""), "body": r.get("body", ""),
                 "href": r.get("href", "")} for r in results]
    except Exception:
        logger.exception("duckduckgo search failed for %r", question[:80])
        return []


def _search_google(question: str, max_results: int = 10) -> list[dict[str, str]]:
    """search via Google (googlesearch-python package)."""
    try:
        from googlesearch import search as gsearch
    except ImportError:
        logger.warning("google search unavailable: pip install googlesearch-python")
        return _search_duckduckgo(question, max_results)  # fallback
    try:
        results = []
        for url in gsearch(question, num_results=max_results):
            results.append({"title": url.split("/")[-1] or url, "body": "", "href": url})
        return results
    except Exception:
        logger.exception("google search failed for %r", question[:80])
        return _search_duckduckgo(question, max_results)


def _search_bing(question: str, max_results: int = 10) -> list[dict[str, str]]:
    """search via Bing Web Search API (requires BING_API_KEY env var)."""
    import os
    api_key = os.environ.get("BING_API_KEY")
    if not api_key:
        logger.warning("bing search unavailable: set BING_API_KEY env var")
        return _search_duckduckgo(question, max_results)  # fallback
    try:
        import urllib.request
        url = (
            f"https://api.bing.microsoft.com/v7.0/search"
            f"?q={urllib.parse.quote(question)}&count={max_results}"
        )
        req = urllib.request.Request(url, headers={"Ocp-Apim-Subscription-Key": api_key})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        return [
            {"title": r.get("name", ""), "body": r.get("snippet", ""),
             "href": r.get("url", "")}
            for r in data.get("webPages", {}).get("value", [])
        ]
    except Exception:
        logger.exception("bing search failed for %r", question[:80])
        return _search_duckduckgo(question, max_results)


_SEARCH_ENGINES = {
    "duckduckgo": _search_duckduckgo,
    "google": _search_google,
    "bing": _search_bing,
}


def _web_search(
    question: str,
    max_results: int = 10,
    engine: str = "duckduckgo",
) -> list[dict[str, str]]:
    """dispatch to the selected search engine. fallback chain ensures results."""
    fn = _SEARCH_ENGINES.get(engine, _search_duckduckgo)
    results = fn(question, max_results)
    logger.warning(
        "web search (%s) for %r returned %d results",
        engine, question[:60], len(results),
    )
    return results


def _format_web_results(results: list[dict[str, str]]) -> str:
    """format web search results into a text block for the generation prompt."""
    if not results:
        return ""
    lines = []
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. **{r['title']}**\n   {r['body']}\n   Source: {r['href']}")
    return "\n\n".join(lines)


# --- generation (reasoning-aware token budgets). ---
#
# with reasoning enabled, gemma 4 produces <think> blocks that consume
# ~500-3000 tokens before the actual content. max_tokens=8192 gives
# ample room for thinking + a thorough answer within the 32k slot.
# with reasoning off, 2048 tokens is enough for a concise answer.

_GEN_MAX_TOKENS = 8192
_GEN_MAX_TOKENS_NO_REASONING = 2048


def _generate_rag_answer(
    question: str,
    context: dict[str, str],
    history: list[dict] | None = None,
    web_results: str = "",
    reasoning: bool = True,
) -> str:
    """generate answer grounded in wiki context (RETRIEVE path)."""
    from llm_client import ContextOverflowError, llm

    max_tok = _GEN_MAX_TOKENS if reasoning else _GEN_MAX_TOKENS_NO_REASONING
    wiki_text = "\n\n---\n\n".join(
        f"# {name}\n{content}" for name, content in context.items()
    )
    conv_block = _build_history_block(history)

    if web_results:
        instruction = (
            "Answer the question using the wiki pages AND the web search results below. "
            "Prioritize web results for up-to-date information. "
            "Use [[wikilinks]] when referencing wiki entities. "
            "For web sources, cite the source name or URL inline."
        )
        web_block = f"\n\nWeb search results:\n{web_results}\n"
    else:
        instruction = (
            "Answer the question using ONLY the wiki pages below. "
            "Use [[wikilinks]] when referencing entities or concepts. "
            "If the wiki lacks the information, say so explicitly."
        )
        web_block = ""

    ground = _ground_truths()
    prompt = (
        f"{ground}"
        f"{instruction}\n\n"
        f"Wiki pages:\n{wiki_text}\n"
        f"{web_block}\n"
        f"{conv_block}"
        f"Question: {question}"
    )

    try:
        return llm(prompt, max_tokens=max_tok, temperature=0.4)
    except ContextOverflowError:
        half = dict(list(context.items())[: len(context) // 2])
        if not half:
            return "(Error: even a single wiki page exceeds the context window.)"
        wiki_text = "\n\n---\n\n".join(
            f"# {name}\n{content}" for name, content in half.items()
        )
        return llm(
            f"{ground}"
            f"{instruction}\n\n"
            f"Wiki pages:\n{wiki_text}\n"
            f"{web_block}\n"
            f"{conv_block}"
            f"Question: {question}",
            max_tokens=max_tok,
            temperature=0.4,
        )


def _generate_direct_answer(
    question: str,
    history: list[dict] | None = None,
    web_results: str = "",
    reasoning: bool = True,
) -> str:
    """generate answer from model's parametric knowledge (DIRECT path)."""
    from llm_client import llm

    max_tok = _GEN_MAX_TOKENS if reasoning else _GEN_MAX_TOKENS_NO_REASONING
    conv_block = _build_history_block(history)

    if web_results:
        instruction = (
            "Answer the question using the web search results below AND your own knowledge. "
            "Prioritize web results for up-to-date facts. "
            "Cite sources by name or URL when using web information."
        )
        web_block = f"\n\nWeb search results:\n{web_results}\n\n"
    else:
        instruction = (
            "Answer the following question using your own knowledge. "
            "Be clear and concise."
        )
        web_block = ""

    ground = _ground_truths()
    return llm(
        f"{ground}"
        f"{instruction}\n\n"
        f"{web_block}"
        f"{conv_block}"
        f"Question: {question}",
        max_tokens=max_tok,
        temperature=0.4,
    )


def _generate_hybrid_answer(
    question: str,
    context: dict[str, str],
    history: list[dict] | None = None,
    web_results: str = "",
    reasoning: bool = True,
) -> str:
    """generate answer using both wiki context and model knowledge (HYBRID)."""
    from llm_client import ContextOverflowError, llm

    max_tok = _GEN_MAX_TOKENS if reasoning else _GEN_MAX_TOKENS_NO_REASONING
    wiki_text = "\n\n---\n\n".join(
        f"# {name}\n{content}" for name, content in context.items()
    )
    conv_block = _build_history_block(history)

    if web_results:
        instruction = (
            "Answer the question using the wiki pages, web search results, AND your own knowledge. "
            "Prioritize web results for up-to-date information. "
            "Use [[wikilinks]] when referencing wiki entities. "
            "For web sources, cite the source name or URL inline. "
            "When adding your own knowledge, prefix with '[General knowledge]'."
        )
        web_block = f"\n\nWeb search results:\n{web_results}\n"
    else:
        instruction = (
            "Answer the question using the wiki pages below AND your own knowledge. "
            "Use [[wikilinks]] when referencing entities from the wiki. "
            "When you add information from your own knowledge that is NOT in the wiki, "
            "prefix those sentences with '[General knowledge]'. "
            "When citing wiki content, reference it naturally with wikilinks."
        )
        web_block = ""

    ground = _ground_truths()
    prompt = (
        f"{ground}"
        f"{instruction}\n\n"
        f"Wiki pages:\n{wiki_text}\n"
        f"{web_block}\n"
        f"{conv_block}"
        f"Question: {question}"
    )

    try:
        return llm(prompt, max_tokens=max_tok, temperature=0.4)
    except ContextOverflowError:
        half = dict(list(context.items())[: len(context) // 2])
        if not half:
            return _generate_direct_answer(question, history, web_results, reasoning)
        wiki_text = "\n\n---\n\n".join(
            f"# {name}\n{content}" for name, content in half.items()
        )
        return llm(
            f"{ground}"
            f"{instruction}\n\n"
            f"Wiki pages:\n{wiki_text}\n"
            f"{web_block}\n"
            f"{conv_block}"
            f"Question: {question}",
            max_tokens=max_tok,
            temperature=0.4,
        )


# --- source attribution. ---

_SOURCE_LABELS = {
    "RETRIEVE": "wiki",
    "DIRECT": "model",
    "HYBRID": "wiki + model",
    "REFUSE": "none",
}

_WEB_SOURCE_LABELS = {
    "RETRIEVE": "wiki + web",
    "DIRECT": "model + web",
    "HYBRID": "wiki + model + web",
    "REFUSE": "none",
}


# --- route execution (shared by all endpoints). ---

def _execute_route(
    question: str,
    route: str,
    history: list[dict] | None = None,
) -> tuple[str, str, dict[str, str], float, float]:
    """run the routed generation path.

    returns (answer, effective_route, context, search_ms, gen_ms).
    """
    context: dict[str, str] = {}
    search_ms = 0.0
    gen_ms = 0.0
    effective_route = route

    if route == "REFUSE":
        answer = (
            "I can't help with that question. Please ask something about "
            "the topics in your wiki or a general knowledge question."
        )
        return answer, effective_route, context, search_ms, gen_ms

    if route == "DIRECT":
        t0 = time.time()
        answer = _generate_direct_answer(question, history)
        gen_ms = (time.time() - t0) * 1000
        return answer, effective_route, context, search_ms, gen_ms

    # RETRIEVE or HYBRID.
    context, _names, search_ms, top_score = _retrieve_context(question)

    # skr guard: weak results → fall back to DIRECT.
    if not context or top_score < _BM25_NOISE_THRESHOLD:
        effective_route = "DIRECT"
        t0 = time.time()
        answer = _generate_direct_answer(question, history)
        gen_ms = (time.time() - t0) * 1000
        return answer, effective_route, {}, search_ms, gen_ms

    t0 = time.time()
    if route == "RETRIEVE":
        answer = _generate_rag_answer(question, context, history)
    else:
        answer = _generate_hybrid_answer(question, context, history)
    gen_ms = (time.time() - t0) * 1000
    return answer, effective_route, context, search_ms, gen_ms


# --- endpoints. ---

@router.post("")
async def query_wiki(body: dict[str, Any]) -> dict[str, Any]:
    """single-turn question (backward-compatible POST endpoint)."""
    question = body.get("question", "").strip()
    save = body.get("save", False)

    if not question:
        raise HTTPException(400, "Question is required.")

    if not check_server_health(LLAMA_URL):
        raise HTTPException(
            503,
            "The LLM server is not running. Start it from the Server panel.",
        )

    t_route = time.time()
    route = _classify_intent(question)
    route_ms = (time.time() - t_route) * 1000

    answer, effective_route, context, search_ms, gen_ms = _execute_route(
        question, route, history=None,
    )

    saved_path = None
    if save and answer and effective_route != "REFUSE":
        saved_path = _save_synthesis(question, answer, context)

    return {
        "answer": answer,
        "sources": list(context.keys()),
        "source": _SOURCE_LABELS.get(effective_route, "unknown"),
        "route": effective_route,
        "route_time_ms": round(route_ms, 1),
        "search_time_ms": round(search_ms, 1),
        "generation_time_ms": round(gen_ms, 1),
        "saved_path": saved_path,
    }


_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


@router.get("/stream")
async def query_stream_get(question: str = "") -> StreamingResponse:
    """single-turn SSE stream (backward-compatible GET endpoint)."""
    if not question.strip():
        raise HTTPException(400, "Question is required.")

    if not check_server_health(LLAMA_URL):
        raise HTTPException(503, "The LLM server is not running.")

    return StreamingResponse(
        _stream_events(question.strip(), history=None),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@router.post("/chat")
async def query_chat(body: ChatRequest) -> StreamingResponse:
    """multi-turn chat with SSE streaming.

    accepts validated conversation history for contextual follow-up answers.
    the frontend sends recent turns; the backend includes them in the
    generation prompt for conversational coherence.
    """
    if not check_server_health(LLAMA_URL):
        raise HTTPException(503, "The LLM server is not running.")

    history_dicts = [{"role": m.role, "content": m.content} for m in body.history]

    return StreamingResponse(
        _stream_events(
            body.question.strip(),
            history=history_dicts,
            save=body.save,
            reasoning=body.reasoning,
            web_search=body.web_search,
            web_results_count=body.web_results_count,
            search_engine=body.search_engine,
        ),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


async def _stream_events(
    question: str,
    history: list[dict] | None = None,
    save: bool = False,
    reasoning: bool = True,
    web_search: bool = False,
    web_results_count: int = 10,
    search_engine: str = "duckduckgo",
):
    """generate SSE events for a query with intent routing.

    blocking llm calls are offloaded to the default thread pool via
    run_in_executor so the fastapi event loop stays responsive for
    concurrent requests (e.g. sidebar health checks, other SSE streams).

    flags:
      reasoning — when true (default), allows gemma 4's <think> mode and
                  uses the full 8192 token budget. when false, caps at 2048.
      web_search — when true, runs a duckduckgo search and includes results
                   in the generation prompt for up-to-date information.
    """
    # ``get_running_loop`` is the non-deprecated accessor. we know we are
    # inside fastapi's event loop because this coroutine is only awaited
    # from request handlers.
    loop = asyncio.get_running_loop()

    # phase 0: classify intent.
    yield _sse("route_start", {
        "question": question,
        "reasoning": reasoning,
        "search_engine": search_engine if web_search else None,
    })

    t_route = time.time()
    route = await loop.run_in_executor(
        None, _classify_intent, question, history,
    )
    route_ms = (time.time() - t_route) * 1000

    yield _sse("route_complete", {
        "route": route,
        "route_time_ms": round(route_ms, 1),
    })

    if route == "REFUSE":
        yield _sse("generation_complete", {
            "answer": "I can't help with that question.",
            "sources": [],
            "source": "none",
            "route": "REFUSE",
        })
        return

    effective_route = route
    context: dict[str, str] = {}

    # phase 1: search (if needed).
    if route in ("RETRIEVE", "HYBRID"):
        yield _sse("search_start", {})

        context, _names, search_ms, top_score = await loop.run_in_executor(
            None, _retrieve_context, question,
        )

        yield _sse("search_complete", {
            "pages_found": len(context),
            "page_names": list(context.keys())[:10],
            "elapsed_ms": round(search_ms, 1),
            "top_score": round(top_score, 4),
        })

        if not context or top_score < _BM25_NOISE_THRESHOLD:
            effective_route = "DIRECT"
            context = {}

    # phase 1b: web search (if enabled).
    web_results_text = ""
    if web_search:
        yield _sse("web_search_start", {"query": question})

        t_web = time.time()
        web_results = await loop.run_in_executor(
            None, _web_search, question, web_results_count, search_engine,
        )
        web_ms = (time.time() - t_web) * 1000

        web_results_text = _format_web_results(web_results)

        yield _sse("web_search_complete", {
            "results_found": len(web_results),
            "elapsed_ms": round(web_ms, 1),
            "snippets": [r.get("title", "") for r in web_results[:5]],
        })

    # select source label based on whether web was used.
    labels = _WEB_SOURCE_LABELS if (web_search and web_results_text) else _SOURCE_LABELS
    source_label = labels.get(effective_route, "unknown")

    # phase 2: generate.
    yield _sse("generation_start", {
        "route": effective_route,
        "source": source_label,
        "pages_in_context": len(context),
        "web_enriched": bool(web_results_text),
    })

    t0 = time.time()
    try:
        if effective_route == "DIRECT":
            answer = await loop.run_in_executor(
                None, _generate_direct_answer, question, history,
                web_results_text, reasoning,
            )
        elif effective_route == "RETRIEVE":
            answer = await loop.run_in_executor(
                None, _generate_rag_answer, question, context, history,
                web_results_text, reasoning,
            )
        else:
            answer = await loop.run_in_executor(
                None, _generate_hybrid_answer, question, context, history,
                web_results_text, reasoning,
            )
    except Exception:
        # full traceback goes to the server log; the client gets a
        # generic message so internal errors (library names, stack
        # frames, filesystem paths) never leak over the wire.
        logger.exception("generation failed for %r", question[:80])
        yield _sse("error", {"message": "Generation failed. See server logs for details."})
        return
    gen_ms = (time.time() - t0) * 1000

    saved_path = None
    if save and answer and effective_route != "REFUSE":
        saved_path = _save_synthesis(question, answer, context)

    # build combined sources: wiki pages + web result titles.
    all_sources = list(context.keys())
    if web_search and web_results_text:
        web_titles = [r.get("title", "") for r in web_results if r.get("title")]
        all_sources.extend(web_titles)

    yield _sse("generation_complete", {
        "answer": answer,
        "sources": all_sources,
        "source": source_label,
        "route": effective_route,
        "generation_time_ms": round(gen_ms, 1),
        "saved_path": saved_path,
    })


def _sse(event: str, data: dict) -> str:
    """format a server-sent event."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _save_synthesis(
    question: str,
    answer: str,
    context: dict[str, str],
) -> str | None:
    """write a synthesis page from a query answer. returns the saved path.

    the slug is aggressively sanitised (``[^\\w\\s]`` stripped, whitespace
    collapsed, 50 char cap), but we still enforce containment: the resolved
    write path MUST live under ``WIKI_DIR / synthesis``. this guards against
    future edits accidentally weakening the slug rule.
    """
    today = date.today().isoformat()
    slug = re.sub(r"[^\w\s]", "", question)[:50].strip().replace(" ", "_")
    if not slug:
        logger.warning("synthesis save skipped: empty slug from %r", question[:80])
        return None

    synthesis_root = (WIKI_DIR / "synthesis").resolve()
    synthesis_root.mkdir(parents=True, exist_ok=True)

    try:
        out = (synthesis_root / f"{slug}.md").resolve()
        out.relative_to(synthesis_root)
    except (OSError, ValueError):
        logger.warning("synthesis save refused: path escapes wiki root (slug=%r)", slug)
        return None

    page_content = "\n".join([
        "---",
        "type: synthesis",
        f"created: {today}",
        f"updated: {today}",
        f"sources: [{', '.join(context.keys())}]",
        "tags: [query]",
        "---",
        "",
        f"# {question}",
        "",
        answer,
        "",
    ])
    out.write_text(page_content)
    return f"wiki/synthesis/{out.name}"


class SaveAnswerRequest(BaseModel):
    question: str = Field(min_length=1, max_length=5000)
    answer: str = Field(min_length=1, max_length=50_000)
    sources: list[str] = Field(default_factory=list)


@router.post("/save")
async def save_answer(body: SaveAnswerRequest) -> dict[str, Any]:
    """save a previously generated answer as a synthesis page."""
    context = {s: "" for s in body.sources}
    saved_path = _save_synthesis(body.question, body.answer, context)
    if saved_path:
        return {"saved_path": saved_path, "message": "Answer saved to wiki."}
    raise HTTPException(500, "Failed to save answer.")
