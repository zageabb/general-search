from __future__ import annotations

import ipaddress
import json
import re
import socket
import threading
import uuid
from copy import deepcopy
from datetime import date, datetime, timezone
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from ddgs import DDGS

from settings_store import PROMPTS, get_settings, render


JOBS: dict[str, dict] = {}
LOCK = threading.Lock()
HEADERS = {"User-Agent": "GeneralSearch/1.0 (+local research app)", "Accept": "text/html,application/xhtml+xml"}


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def list_models():
    settings = get_settings()
    response = requests.get(settings["ollama_url"].rstrip("/") + "/api/tags", timeout=8)
    response.raise_for_status()
    return [row.get("name") for row in response.json().get("models", []) if row.get("name")]


def start_job(app, query, history, model, allowed_only):
    job_id = uuid.uuid4().hex
    job = {"id": job_id, "status": "queued", "phase": "Queued", "events": [], "sources": [], "steps": [], "message": None, "error": None, "created_at": now(), "started_at": None, "completed_at": None}
    with LOCK:
        JOBS[job_id] = job
        finished = [key for key, value in JOBS.items() if value["status"] in {"completed", "failed"}]
        for key in finished[:-50]:
            JOBS.pop(key, None)
    threading.Thread(target=_run, args=(app, job_id, query, history, model, allowed_only), daemon=True).start()
    return deepcopy(job)


def event(job_id, kind, status, label, detail="", url="", phase=""):
    with LOCK:
        job = JOBS.get(job_id)
        if not job:
            return
        job["events"].append({"sequence": len(job["events"]) + 1, "timestamp": now(), "kind": kind, "status": status, "label": label[:500], "detail": detail[:1000], "url": url[:2000]})
        job["events"] = job["events"][-250:]
        if phase:
            job["phase"] = phase


def update(job_id, **values):
    with LOCK:
        if job_id in JOBS:
            JOBS[job_id].update(values)


def _run(app, job_id, query, history, model, allowed_only):
    update(job_id, status="running", phase="Planning research", started_at=now())
    try:
        settings = get_settings()
        model = model or settings["model"]
        market = ", ".join(filter(None, [settings["market_city"], settings["market_region"], settings["market_country"]])) or "Global"
        allowed = domains(settings["allowed_domains"]) if allowed_only else []
        blocked = domains(settings["blocked_domains"])
        scope = "Only: " + ", ".join(allowed) if allowed else "Any public website except blocked domains"
        prompts = PROMPTS.load()
        context = "\n\n".join(f"{x['role'].title()}: {x['content']}" for x in history[-12:])
        effective_query = query if not context else f"Conversation context:\n{context}\n\nCurrent request:\n{query}"
        planning = render(prompts["planning"], market=market, scope=scope, query=effective_query)
        event(job_id, "phase", "running", "Understanding and improving the request", phase="Planning response")
        parsed = ollama_json(settings["ollama_url"], model, planning)
        rewritten_question = clean_text(parsed.get("rewritten_question"), effective_query)
        requirements = clean_items(parsed.get("requirements", []), 8)
        subquestions = clean_items(parsed.get("subquestions", []), 5)
        needs_web = parsed.get("needs_web") is True
        queries = clean_queries(parsed.get("queries", []))
        if needs_web and not queries:
            queries = [query, f"{query} authoritative source", f"{query} {market}"]
        queries = queries[:6]
        route = "web research" if needs_web else "model knowledge"
        event(job_id, "reasoning", "summary", f"Using {route}", rewritten_question, phase="Planning complete")
        if subquestions:
            event(job_id, "reasoning", "summary", f"Identified {len(subquestions)} supporting questions", "; ".join(subquestions))

        if not needs_web:
            direct_prompt = direct_answer_prompt(prompts, settings, market, effective_query, rewritten_question,
                                                 requirements, subquestions, "Not needed for this request")
            event(job_id, "phase", "running", "Answering from model knowledge", phase="Producing answer")
            answer = ollama_text(settings["ollama_url"], model, direct_prompt)
            answer = review_answer(job_id, prompts, settings, model, effective_query, rewritten_question,
                                   requirements, subquestions, answer, "No web evidence (model knowledge answer)")
            update(job_id, status="completed", phase="Complete", message=answer, sources=[],
                   steps=[f"Ollama model: {model}", "Route: model knowledge", f"Clarified request: {rewritten_question}", "Final answer reviewed"], completed_at=now())
            event(job_id, "phase", "returned", "Answer completed", phase="Complete")
            return

        evidence = []
        seen = set()
        for round_number in range(1, settings["max_search_rounds"] + 1):
            event(job_id, "phase", "running", f"Research round {round_number}", phase=f"Searching — round {round_number}")
            candidates = []
            for search_query in queries:
                event(job_id, "search", "initiated", search_query)
                try:
                    rows = list(DDGS(timeout=12).text(search_query, region="wt-wt", safesearch="moderate", max_results=settings["results_per_query"], backend=settings["search_backend"]) or [])
                    event(job_id, "search", "returned", search_query, f"{len(rows)} results returned")
                except Exception as exc:
                    event(job_id, "search", "failed", search_query, str(exc))
                    continue
                for row in rows:
                    url = str(row.get("href") or "")
                    host = hostname(url)
                    if not host or url in seen or any(host == d or host.endswith("." + d) for d in blocked):
                        continue
                    if allowed and not any(host == d or host.endswith("." + d) for d in allowed):
                        continue
                    seen.add(url)
                    candidates.append((str(row.get("title") or url), url, str(row.get("body") or ""), search_query))
            for title, url, snippet, source_query in candidates:
                if len(evidence) >= settings["max_pages_to_read"]:
                    break
                source_id = len(evidence) + 1
                event(job_id, "site", "initiated", title, f"Opening source {source_id}", url, "Reading sites")
                try:
                    text = read_page(url) or snippet
                except requests.RequestException as exc:
                    event(job_id, "site", "failed", title, request_error(exc), url)
                    text = snippet
                except Exception as exc:
                    event(job_id, "site", "failed", title, str(exc), url)
                    text = snippet
                if len(text.strip()) < 40:
                    event(job_id, "site", "unreadable", title, "No readable evidence", url)
                    continue
                evidence.append({"source_id": source_id, "title": title, "url": url, "query": source_query, "text": text[:12_000]})
                event(job_id, "site", "returned", title, f"Source {source_id} retained", url)
            if evidence or round_number == settings["max_search_rounds"]:
                break

        if not evidence:
            direct_prompt = direct_answer_prompt(prompts, settings, market, effective_query, rewritten_question,
                                                 requirements, subquestions,
                                                 "Web research returned no readable evidence; answer cautiously from model knowledge")
            event(job_id, "phase", "running", "Web unavailable; using model knowledge", phase="Producing answer")
            answer = ollama_text(settings["ollama_url"], model, direct_prompt)
            answer = review_answer(job_id, prompts, settings, model, effective_query, rewritten_question,
                                   requirements, subquestions, answer, "No readable web evidence (knowledge fallback)")
            update(job_id, status="completed", phase="Complete", message=answer, sources=[],
                   steps=[f"Ollama model: {model}", "Route: knowledge fallback after web failure", f"Clarified request: {rewritten_question}", "Final answer reviewed"], completed_at=now())
            event(job_id, "phase", "returned", "Fallback answer completed", phase="Complete")
            return
        evidence_text = "\n\n---\n\n".join(f"[{x['source_id']}] {x['title']}\nURL: {x['url']}\nFound via: {x['query']}\nContent: {x['text']}" for x in evidence)
        answer_prompt = render(prompts["answer"], date=date.today(), market=market, scope=scope, query=effective_query,
                               rewritten_question=rewritten_question, requirements="; ".join(requirements) or "None specified",
                               subquestions="; ".join(subquestions) or "None", evidence=evidence_text[:60_000])
        instructions = settings["general_search_instructions"].strip()
        if instructions:
            answer_prompt = f"Persistent user instructions:\n{instructions}\n\n{answer_prompt}"
        event(job_id, "phase", "running", f"Synthesising from {len(evidence)} sources", phase="Producing answer")
        answer = ollama_text(settings["ollama_url"], model, answer_prompt)
        answer = review_answer(job_id, prompts, settings, model, effective_query, rewritten_question,
                               requirements, subquestions, answer, evidence_text)
        sources = [{"source_id": x["source_id"], "title": x["title"], "url": x["url"]} for x in evidence]
        update(job_id, status="completed", phase="Complete", message=answer, sources=sources, steps=[f"Ollama model: {model}", f"Planned {len(queries)} searches", f"Retained {len(evidence)} readable sources", "Final answer reviewed"], completed_at=now())
        event(job_id, "phase", "returned", "Research answer completed", phase="Complete")
    except Exception as exc:
        update(job_id, status="failed", phase="Failed", error=str(exc), completed_at=now())
        event(job_id, "phase", "failed", f"Search failed: {exc}", phase="Failed")


def ollama_text(base_url, model, prompt):
    response = requests.post(base_url.rstrip("/") + "/api/generate", json={"model": model, "prompt": prompt, "stream": False}, timeout=300)
    response.raise_for_status()
    text = str(response.json().get("response") or "").strip()
    if not text:
        raise RuntimeError("Ollama returned an empty response.")
    return text


def ollama_json(base_url, model, prompt):
    response = requests.post(base_url.rstrip("/") + "/api/generate", json={"model": model, "prompt": prompt, "stream": False, "format": "json"}, timeout=180)
    response.raise_for_status()
    try:
        return json.loads(response.json().get("response") or "{}")
    except json.JSONDecodeError:
        return {}


def read_page(url):
    if not public_url(url):
        return ""
    response = requests.get(url, headers=HEADERS, timeout=15, allow_redirects=True)
    response.raise_for_status()
    if "html" not in response.headers.get("content-type", "").lower():
        return ""
    soup = BeautifulSoup(response.content[:5_000_000], "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "noscript"]):
        tag.decompose()
    return " ".join(soup.get_text(" ", strip=True).split())


def request_error(exc):
    """Return a useful, bounded message without leaking a response body."""
    response = getattr(exc, "response", None)
    if response is not None:
        return f"HTTP {response.status_code}: {response.reason or 'request failed'}"
    return str(exc)


def public_url(url):
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    try:
        for info in socket.getaddrinfo(parsed.hostname, None):
            if ipaddress.ip_address(info[4][0]).is_private or ipaddress.ip_address(info[4][0]).is_loopback:
                return False
    except (OSError, ValueError):
        return False
    return True


def hostname(url):
    try:
        return (urlparse(url).hostname or "").lower().removeprefix("www.")
    except ValueError:
        return ""


def domains(value):
    return list(dict.fromkeys(filter(None, (hostname(x if "://" in x else "https://" + x) for x in re.split(r"[\n,]+", str(value))))))


def clean_queries(values):
    return list(dict.fromkeys(" ".join(str(x).split())[:300] for x in values if str(x).strip()))


def clean_items(values, limit):
    if not isinstance(values, list):
        return []
    return list(dict.fromkeys(" ".join(str(x).split())[:500] for x in values if str(x).strip()))[:limit]


def clean_text(value, fallback):
    value = " ".join(str(value or "").split()).strip()
    return value[:4000] or fallback


def direct_answer_prompt(prompts, settings, market, query, rewritten_question, requirements, subquestions, web_status):
    prompt = render(prompts["direct_answer"], date=date.today(), market=market, query=query,
                    rewritten_question=rewritten_question, requirements="; ".join(requirements) or "None specified",
                    subquestions="; ".join(subquestions) or "None", web_status=web_status)
    instructions = settings["general_search_instructions"].strip()
    return f"Persistent user instructions:\n{instructions}\n\n{prompt}" if instructions else prompt


def review_answer(job_id, prompts, settings, model, query, rewritten_question, requirements, subquestions, answer, evidence):
    event(job_id, "phase", "running", "Checking the answer against the request", phase="Reviewing answer")
    prompt = render(prompts["review"], query=query, rewritten_question=rewritten_question,
                    requirements="; ".join(requirements) or "None specified",
                    subquestions="; ".join(subquestions) or "None", evidence=str(evidence)[:40_000], answer=answer[:30_000])
    try:
        reviewed = ollama_json(settings["ollama_url"], model, prompt)
        final_answer = str(reviewed.get("final_answer") or "").strip()
        issues = clean_items(reviewed.get("issues", []), 8)
        detail = "; ".join(issues) if issues else "No material omissions found"
        if not final_answer:
            event(job_id, "reasoning", "failed", "Final review returned no answer", "Using the original draft")
            return answer
        status = "passed" if reviewed.get("answered") is True else "revised"
        event(job_id, "reasoning", "returned", f"Final answer review {status}", detail)
        return final_answer
    except Exception as exc:
        event(job_id, "reasoning", "failed", "Final answer review unavailable", str(exc))
        return answer
