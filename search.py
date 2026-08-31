from __future__ import annotations

import ipaddress
import json
import math
import re
import socket
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import date, datetime, timezone
from io import BytesIO
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from ddgs import DDGS

from settings_store import PROMPTS, get_settings, record_domain_verdict, render


JOBS: dict[str, dict] = {}
LOCK = threading.Lock()
HEADERS = {"User-Agent": "GeneralSearch/1.0 (+local research app)",
           "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9"}
MAX_WEB_BYTES = 8_000_000
MAX_REDIRECTS = 5


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def list_models():
    settings = get_settings()
    response = requests.get(settings["ollama_url"].rstrip("/") + "/api/tags", timeout=8)
    response.raise_for_status()
    return [row.get("name") for row in response.json().get("models", []) if row.get("name")]


def start_job(app, query, history, model, allowed_only, uploaded_context=""):
    job_id = uuid.uuid4().hex
    job = {"id": job_id, "status": "queued", "phase": "Queued", "events": [], "sources": [], "steps": [], "message": None, "error": None, "created_at": now(), "started_at": None, "completed_at": None}
    with LOCK:
        JOBS[job_id] = job
        finished = [key for key, value in JOBS.items() if value["status"] in {"completed", "failed"}]
        for key in finished[:-50]:
            JOBS.pop(key, None)
    threading.Thread(target=_run, args=(app, job_id, query, history, model, allowed_only, uploaded_context), daemon=True).start()
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


def _run(app, job_id, query, history, model, allowed_only, uploaded_context=""):
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
        if uploaded_context:
            effective_query = f"{effective_query}\n\n{uploaded_context}"
            event(job_id, "document", "returned", "Included uploaded document context", f"{len(uploaded_context):,} characters available")
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
        attempted_queries = []
        current_queries = queries
        for round_number in range(1, settings["max_search_rounds"] + 1):
            event(job_id, "phase", "running", f"Research round {round_number}", phase=f"Searching — round {round_number}")
            candidates = []
            for search_query in current_queries:
                attempted_queries.append(search_query)
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
                    candidates.append({"title": str(row.get("title") or url), "url": url,
                                       "snippet": str(row.get("body") or ""), "query": search_query,
                                       "published_at": str(row.get("date") or row.get("published") or "")})
            ranked = rank_candidates(candidates, rewritten_question, requirements, subquestions)
            ranked = embedding_rerank(job_id, settings, ranked,
                                      research_text(rewritten_question, requirements, subquestions))
            remaining_rounds = settings["max_search_rounds"] - round_number + 1
            remaining_pages = settings["max_pages_to_read"] - len(evidence)
            round_budget = remaining_pages if remaining_rounds == 1 else max(1, math.ceil(remaining_pages / remaining_rounds))
            retained_this_round = 0
            shortlist_size = min(len(ranked), max(round_budget * 3, settings["max_fetch_workers"]))
            shortlist = ranked[:shortlist_size]
            fetched = fetch_pages(job_id, shortlist, settings["max_fetch_workers"])
            for candidate in shortlist:
                if len(evidence) >= settings["max_pages_to_read"] or retained_this_round >= round_budget:
                    break
                title, url, snippet, source_query = (candidate[key] for key in ("title", "url", "snippet", "query"))
                source_id = len(evidence) + 1
                page = fetched.get(url, {})
                if page.get("error"):
                    event(job_id, "site", "failed", title, page["error"], url)
                    continue
                text = page.get("text") or snippet
                if len(text.strip()) < 40:
                    event(job_id, "site", "unreadable", title, "No readable evidence", url)
                    continue
                passages = best_passages(text, research_text(rewritten_question, requirements, subquestions), limit=5)
                focused_text = "\n\n".join(passages) or text[:12_000]
                verdict, reason, claims = analyse_source(prompts, settings, model, source_query, title, url, focused_text)
                if verdict != "useful":
                    if verdict == "unusable":
                        reason = f"Not retained: {reason}"
                    event(job_id, "site", "failed", title, reason, url)
                    continue
                record_domain_verdict(hostname(url), True)
                evidence.append({"source_id": source_id, "title": title, "url": url, "query": source_query,
                                 "passages": passages, "claims": claims, "text": focused_text[:12_000],
                                 "relevance": candidate["score"],
                                 "published_at": page.get("published_at") or candidate.get("published_at", ""),
                                 "content_type": page.get("content_type", "")})
                retained_this_round += 1
                event(job_id, "site", "returned", title, f"Source {source_id} retained · {reason}", url)
            if round_number == settings["max_search_rounds"] or len(evidence) >= settings["max_pages_to_read"]:
                break
            coverage = assess_coverage(prompts, settings, model, rewritten_question, requirements, subquestions,
                                       attempted_queries, evidence)
            gaps = clean_items(coverage.get("gaps", []), 6)
            follow_ups = clean_queries(coverage.get("queries", []))[:4]
            if coverage.get("complete") is True:
                event(job_id, "reasoning", "returned", "Evidence coverage is sufficient",
                      "; ".join(clean_items(coverage.get("covered", []), 6)) or "Core request supported")
                break
            if gaps:
                event(job_id, "reasoning", "summary", f"Research found {len(gaps)} evidence gap(s)", "; ".join(gaps))
            if not follow_ups:
                if evidence:
                    event(job_id, "reasoning", "summary", "No productive follow-up search identified",
                          "Proceeding with the available evidence and explicit uncertainty")
                    break
                follow_ups = [f"{query} primary source", f"{query} official information"]
            current_queries = follow_ups

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
        evidence_text = evidence_ledger(evidence)
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
        answer = verify_citations(job_id, prompts, settings, model, rewritten_question, answer, evidence_text, evidence)
        sources = [{"source_id": x["source_id"], "title": x["title"], "url": x["url"]} for x in evidence]
        update(job_id, status="completed", phase="Complete", message=answer, sources=sources,
               steps=[f"Ollama model: {model}", f"Ran {len(attempted_queries)} targeted searches",
                      f"Retained {len(evidence)} ranked sources", "Evidence coverage and citations reviewed"], completed_at=now())
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


def fetch_pages(job_id, candidates, workers):
    results = {}
    with ThreadPoolExecutor(max_workers=max(1, min(int(workers), 8))) as executor:
        future_map = {}
        for candidate in candidates:
            event(job_id, "site", "initiated", candidate["title"],
                  f"Opening candidate ranked {candidate['rank']} · relevance {candidate['score']:.2f}",
                  candidate["url"], "Reading sites")
            future_map[executor.submit(fetch_page, candidate["url"])] = candidate["url"]
        for future in as_completed(future_map):
            url = future_map[future]
            try:
                results[url] = future.result()
            except Exception as exc:
                results[url] = {"text": "", "error": request_error(exc)}
    return results


def fetch_page(url):
    """Fetch a bounded public HTML or PDF page, validating every redirect target."""
    current = url
    response = None
    for _ in range(MAX_REDIRECTS + 1):
        if not public_url(current):
            return {"text": "", "error": "Blocked non-public or invalid URL"}
        response = requests.get(current, headers=HEADERS, timeout=(5, 20), allow_redirects=False, stream=True)
        if response.status_code in {301, 302, 303, 307, 308}:
            location = response.headers.get("location")
            response.close()
            if not location:
                return {"text": "", "error": "Redirect response had no destination"}
            current = urljoin(current, location)
            continue
        break
    else:
        return {"text": "", "error": f"Too many redirects (limit {MAX_REDIRECTS})"}
    try:
        response.raise_for_status()
        declared_size = int(response.headers.get("content-length") or 0)
        if declared_size > MAX_WEB_BYTES:
            return {"text": "", "error": f"Page exceeds the {MAX_WEB_BYTES // 1_000_000} MB download limit"}
        chunks, size = [], 0
        for chunk in response.iter_content(chunk_size=65_536):
            if not chunk:
                continue
            size += len(chunk)
            if size > MAX_WEB_BYTES:
                return {"text": "", "error": f"Page exceeds the {MAX_WEB_BYTES // 1_000_000} MB download limit"}
            chunks.append(chunk)
        payload = b"".join(chunks)
        content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
        if content_type == "application/pdf" or payload.startswith(b"%PDF-"):
            text = extract_pdf(payload)
            return {"text": text, "url": current, "content_type": "application/pdf", "published_at": ""}
        if "html" not in content_type and "xhtml" not in content_type:
            return {"text": "", "error": f"Unsupported web content type: {content_type or 'unknown'}"}
        text, published_at = extract_html(payload)
        return {"text": text, "url": current, "content_type": content_type, "published_at": published_at}
    finally:
        response.close()


def extract_pdf(payload):
    from pypdf import PdfReader

    reader = PdfReader(BytesIO(payload))
    sections, size = [], 0
    for page_number, page in enumerate(reader.pages, 1):
        if page_number > 100:
            break
        text = " ".join((page.extract_text() or "").split())
        if not text:
            continue
        section = f"Page {page_number}: {text}"
        sections.append(section)
        size += len(section)
        if size >= 120_000:
            break
    return "\n".join(sections)[:120_000]


def extract_html(payload):
    soup = BeautifulSoup(payload, "html.parser")
    published_at = ""
    for attributes in ({"property": "article:published_time"}, {"name": "date"},
                       {"name": "datePublished"}, {"itemprop": "datePublished"}):
        tag = soup.find("meta", attrs=attributes)
        if tag and tag.get("content"):
            published_at = str(tag["content"])[:100]
            break
    for tag in soup(["script", "style", "nav", "footer", "noscript"]):
        tag.decompose()
    blocks = [" ".join(block.split()) for block in soup.get_text("\n", strip=True).splitlines()]
    return "\n".join(block for block in blocks if len(block) >= 20)[:120_000], published_at


def read_page(url):
    """Backward-compatible text-only page reader."""
    return fetch_page(url).get("text", "")


def request_error(exc):
    """Return a useful, bounded message without leaking a response body."""
    response = getattr(exc, "response", None)
    if response is not None:
        return f"HTTP {response.status_code}: {response.reason or 'request failed'}"
    return str(exc)


def public_url(url):
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        return False
    try:
        for info in socket.getaddrinfo(parsed.hostname, None):
            if not ipaddress.ip_address(info[4][0]).is_global:
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


STOP_WORDS = {"a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "how", "in", "is", "it",
              "of", "on", "or", "that", "the", "this", "to", "was", "what", "when", "where", "which", "who",
              "why", "will", "with", "you", "your"}


def terms(value):
    return {word for word in re.findall(r"[a-z0-9][a-z0-9_-]{1,}", str(value).lower()) if word not in STOP_WORDS}


def research_text(question, requirements, subquestions):
    return " ".join([question, *requirements, *subquestions])


def rank_candidates(candidates, question, requirements=None, subquestions=None):
    """Rank search results for relevance, authority, and source diversity."""
    target = terms(research_text(question, requirements or [], subquestions or []))
    scored = []
    for candidate in candidates:
        title_terms = terms(candidate.get("title", ""))
        snippet_terms = terms(candidate.get("snippet", ""))
        query_terms = terms(candidate.get("query", ""))
        host = hostname(candidate.get("url", ""))
        overlap = len(target & title_terms) * 3 + len(target & snippet_terms) + len(query_terms & (title_terms | snippet_terms))
        denominator = max(1, len(target))
        authority = 1.25 if (host.endswith(".gov") or host.endswith(".gov.uk") or host.endswith(".edu") or
                             host.endswith(".ac.uk")) else 0.0
        path = urlparse(candidate.get("url", "")).path.lower()
        primary_hint = 0.6 if any(token in path for token in ("/docs", "/documentation", "/research", "/report", "/news")) else 0.0
        freshness = freshness_score(candidate.get("published_at", "")) if freshness_sensitive(question) else 0.0
        candidate = dict(candidate)
        candidate["score"] = round(overlap / denominator + authority + primary_hint + freshness, 3)
        scored.append(candidate)
    scored.sort(key=lambda item: (-item["score"], item.get("title", "").lower()))
    return diversify(scored)


def diversify(scored):
    # Interleave domains so one publisher cannot consume the reading budget.
    ranked, pending, domain_counts = [], scored[:], {}
    while pending:
        best_index = max(range(len(pending)), key=lambda index: pending[index]["score"] - domain_counts.get(hostname(pending[index]["url"]), 0) * 0.75)
        item = pending.pop(best_index)
        host = hostname(item["url"])
        domain_counts[host] = domain_counts.get(host, 0) + 1
        item["rank"] = len(ranked) + 1
        ranked.append(item)
    return ranked


def freshness_sensitive(query):
    query_terms = terms(query)
    current_year = str(date.today().year)
    return bool(query_terms & {"current", "currently", "latest", "recent", "today", "news", "price", "prices",
                               "law", "laws", "regulation", "regulations", "schedule", current_year})


def freshness_score(value):
    raw = str(value or "").strip()
    if not raw:
        return 0.0
    try:
        published = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        age_days = max(0, (datetime.now(timezone.utc) - published).days)
    except ValueError:
        match = re.search(r"\b(20\d{2})\b", raw)
        if not match:
            return 0.0
        age_days = max(0, (date.today().year - int(match.group(1))) * 365)
    if age_days <= 30:
        return 1.0
    if age_days <= 180:
        return 0.7
    if age_days <= 365:
        return 0.4
    if age_days <= 730:
        return 0.1
    return -0.25


def embedding_rerank(job_id, settings, candidates, query):
    model = str(settings.get("embedding_model") or "").strip()
    if not model or len(candidates) < 2:
        return candidates
    inputs = [query] + [f"{item.get('title', '')}\n{item.get('snippet', '')}" for item in candidates]
    try:
        response = requests.post(settings["ollama_url"].rstrip("/") + "/api/embed",
                                 json={"model": model, "input": inputs}, timeout=90)
        response.raise_for_status()
        vectors = response.json().get("embeddings") or []
        if len(vectors) != len(inputs):
            raise ValueError("embedding response did not contain every requested vector")
        query_vector = vectors[0]
        reranked = []
        for candidate, vector in zip(candidates, vectors[1:]):
            candidate = dict(candidate)
            candidate["semantic_similarity"] = round(cosine_similarity(query_vector, vector), 4)
            candidate["score"] = round(candidate["score"] + candidate["semantic_similarity"] * 2, 3)
            reranked.append(candidate)
        reranked.sort(key=lambda item: (-item["score"], item.get("title", "").lower()))
        event(job_id, "reasoning", "returned", f"Semantically reranked {len(reranked)} results",
              f"Embedding model: {model}")
        return diversify(reranked)
    except Exception as exc:
        event(job_id, "reasoning", "failed", "Embedding reranking unavailable",
              f"Using lexical ranking: {request_error(exc)}")
        return candidates


def cosine_similarity(left, right):
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(float(a) * float(b) for a, b in zip(left, right))
    left_norm = math.sqrt(sum(float(value) ** 2 for value in left))
    right_norm = math.sqrt(sum(float(value) ** 2 for value in right))
    return dot / (left_norm * right_norm) if left_norm and right_norm else 0.0


def best_passages(content, query, limit=5, max_chars=9_000):
    """Return the most query-relevant, de-duplicated passages from a page."""
    target = terms(query)
    raw = [" ".join(part.split()) for part in re.split(r"\n{1,}|(?<=[.!?])\s+(?=[A-Z0-9])", str(content))]
    passages = [part for part in raw if 40 <= len(part) <= 2_500]
    if not passages:
        passages = [" ".join(str(content).split())[:max_chars]] if str(content).strip() else []
    scored = []
    for index, passage in enumerate(passages):
        passage_terms = terms(passage)
        coverage = len(target & passage_terms) / max(1, len(target))
        density = len(target & passage_terms) / max(1, len(passage_terms))
        number_bonus = 0.08 if re.search(r"\b\d[\d,.%]*\b", passage) else 0
        scored.append((coverage * 3 + density + number_bonus, index, passage))
    selected, selected_terms, size = [], [], 0
    for score, index, passage in sorted(scored, key=lambda row: (-row[0], row[1])):
        p_terms = terms(passage)
        if any(len(p_terms & prior) / max(1, len(p_terms | prior)) > 0.82 for prior in selected_terms):
            continue
        if size + len(passage) > max_chars and selected:
            continue
        selected.append((index, passage))
        selected_terms.append(p_terms)
        size += len(passage)
        if len(selected) >= limit:
            break
    return [passage for _, passage in sorted(selected)]


def evidence_ledger(evidence):
    rows = []
    for item in evidence:
        claims = "\n".join(f"- {claim}" for claim in item.get("claims", [])) or "- No claims pre-extracted"
        passages = "\n\n".join(f"Passage {index}: {passage}" for index, passage in enumerate(item.get("passages", []), 1))
        published = f"\nPublished: {item['published_at']}" if item.get("published_at") else ""
        rows.append(f"[{item['source_id']}] {item['title']}\nURL: {item['url']}\nFound via: {item['query']}{published}\n"
                    f"Extracted claims:\n{claims}\nRelevant passages:\n{passages or item.get('text', '')}")
    return "\n\n---\n\n".join(rows)


def clean_queries(values):
    if not isinstance(values, list):
        return []
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


def assess_coverage(prompts, settings, model, rewritten_question, requirements, subquestions, queries, evidence):
    if not evidence:
        return {"complete": False, "covered": [], "gaps": ["No readable evidence retained"], "queries": []}
    prompt = render(prompts["research_review"], rewritten_question=rewritten_question,
                    requirements="; ".join(requirements) or "None specified",
                    subquestions="; ".join(subquestions) or "None", queries="; ".join(queries),
                    evidence=evidence_ledger(evidence)[:35_000])
    try:
        return ollama_json(settings["ollama_url"], model, prompt)
    except Exception:
        return {"complete": False, "covered": [], "gaps": [], "queries": []}


def verify_citations(job_id, prompts, settings, model, rewritten_question, answer, evidence_text, evidence):
    event(job_id, "phase", "running", "Validating claims and citations", phase="Verifying citations")
    valid_ids = {str(item["source_id"]) for item in evidence}
    cited_ids = set(re.findall(r"\[(\d+)\]", answer))
    invalid_ids = sorted(cited_ids - valid_ids)
    if invalid_ids:
        event(job_id, "reasoning", "summary", "Draft contained invalid citation IDs", ", ".join(invalid_ids))
    prompt = render(prompts["citation_review"], rewritten_question=rewritten_question,
                    evidence=evidence_text[:55_000], answer=answer[:30_000])
    try:
        result = ollama_json(settings["ollama_url"], model, prompt)
        final_answer = str(result.get("final_answer") or "").strip()
        issues = clean_items(result.get("issues", []), 8)
        if not final_answer:
            event(job_id, "reasoning", "failed", "Citation verification returned no answer", "Using reviewed draft")
            return answer
        final_ids = set(re.findall(r"\[(\d+)\]", final_answer))
        if final_ids - valid_ids:
            event(job_id, "reasoning", "failed", "Citation verification introduced invalid IDs", "Using reviewed draft")
            return answer
        event(job_id, "reasoning", "returned", "Citation verification passed" if result.get("valid") is True else "Citations corrected",
              "; ".join(issues) if issues else "Claims checked against retained passages")
        return final_answer
    except Exception as exc:
        event(job_id, "reasoning", "failed", "Citation verification unavailable", str(exc))
        return answer


def analyse_source(prompts, settings, model, query, title, url, content):
    prompt = render(prompts["source_review"], query=query, title=title, url=url, content=content[:12_000])
    try:
        result = ollama_json(settings["ollama_url"], model, prompt)
    except Exception as exc:
        return "review_failed", f"Quality check failed: {exc}", []
    verdict = str(result.get("verdict") or "").strip().lower()
    reason = clean_text(result.get("reason"), "No useful query-related evidence found")[:300]
    claims = clean_items(result.get("claims", []), 5)
    return ("useful", reason, claims) if verdict == "useful" else ("unusable", reason, [])


def review_source(prompts, settings, model, query, title, url, content):
    """Backward-compatible two-value source review API."""
    verdict, reason, _ = analyse_source(prompts, settings, model, query, title, url, content)
    return verdict, reason
