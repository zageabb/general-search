from __future__ import annotations

import json
import re

import requests
from bs4 import BeautifulSoup

import search
from retrieval_cache import load_page, store_page


MAX_STRUCTURED_LINES = 80
MAX_TABLES = 12
MAX_TABLE_ROWS = 40
MAX_OUTPUT_CHARS = 120_000

META_FIELDS = {
    "description": "description",
    "og:title": "title",
    "og:description": "description",
    "og:type": "page type",
    "og:site_name": "site",
    "article:published_time": "published",
    "article:modified_time": "modified",
    "product:price:amount": "price",
    "product:price:currency": "currency",
    "product:availability": "availability",
    "twitter:title": "title",
    "twitter:description": "description",
}

STRUCTURED_FIELDS = (
    "@type", "name", "headline", "description", "brand", "model", "sku", "mpn",
    "category", "url", "price", "lowPrice", "highPrice", "priceCurrency",
    "availability", "datePublished", "dateModified", "startDate", "endDate",
    "operatingSystem", "applicationCategory", "version",
)


def clean_scalar(value, limit=700):
    if isinstance(value, dict):
        value = value.get("name") or value.get("@id") or value.get("url") or ""
    elif isinstance(value, list):
        value = ", ".join(clean_scalar(item, 200) for item in value[:6])
    return " ".join(str(value or "").split())[:limit]


def append_unique(lines, seen, value):
    value = clean_scalar(value, 2_000)
    if value and value not in seen:
        seen.add(value)
        lines.append(value)


def json_nodes(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from json_nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from json_nodes(child)


def extract_jsonld(soup):
    lines, seen, dates = [], set(), []
    for script in soup.find_all("script", attrs={"type": re.compile(r"application/ld\+json", re.I)}):
        raw = script.string or script.get_text(" ", strip=True)
        if not raw or len(raw) > 2_000_000:
            continue
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        for node in json_nodes(payload):
            if len(lines) >= MAX_STRUCTURED_LINES:
                break
            fields = []
            for key in STRUCTURED_FIELDS:
                value = node.get(key)
                if value not in (None, "", [], {}):
                    fields.append(f"{key}={clean_scalar(value)}")
            if fields:
                append_unique(lines, seen, "Structured item: " + "; ".join(fields))
            published = node.get("datePublished") or node.get("dateModified")
            if published:
                dates.append(clean_scalar(published, 100))
        if len(lines) >= MAX_STRUCTURED_LINES:
            break
    return lines, (dates[0] if dates else "")


def extract_metadata(soup):
    lines, seen = [], set()
    for tag in soup.find_all("meta"):
        key = clean_scalar(tag.get("property") or tag.get("name") or tag.get("itemprop"), 120).lower()
        content = clean_scalar(tag.get("content"), 1_000)
        label = META_FIELDS.get(key)
        if label and content:
            append_unique(lines, seen, f"{label}: {content}")

    for itemprop in (
        "name", "model", "sku", "mpn", "brand", "price", "priceCurrency",
        "availability", "datePublished", "dateModified",
    ):
        for tag in soup.find_all(attrs={"itemprop": itemprop})[:4]:
            value = clean_scalar(tag.get("content") or tag.get_text(" ", strip=True), 700)
            if value:
                append_unique(lines, seen, f"{itemprop}: {value}")
    return lines


def extract_tables(soup):
    sections = []
    for table_index, table in enumerate(soup.find_all("table")[:MAX_TABLES], 1):
        rows = []
        for row in table.find_all("tr")[:MAX_TABLE_ROWS]:
            cells = [
                clean_scalar(cell.get_text(" ", strip=True), 500)
                for cell in row.find_all(["th", "td"])
            ]
            cells = [cell for cell in cells if cell]
            if cells:
                rows.append(" | ".join(cells))
        if rows:
            sections.append(f"Table {table_index}:\n" + "\n".join(rows))
    return sections


def extract_html(payload):
    """Extract generic structured evidence, tables and readable page text."""
    soup = BeautifulSoup(payload, "html.parser")
    structured_lines, structured_date = extract_jsonld(soup)
    metadata_lines = extract_metadata(soup)
    table_sections = extract_tables(soup)

    published_at = structured_date
    if not published_at:
        for attributes in (
            {"property": "article:published_time"},
            {"name": "date"},
            {"name": "datePublished"},
            {"itemprop": "datePublished"},
        ):
            tag = soup.find("meta", attrs=attributes)
            if tag and tag.get("content"):
                published_at = clean_scalar(tag["content"], 100)
                break

    for tag in soup(["script", "style", "nav", "footer", "noscript", "svg"]):
        tag.decompose()

    heading_lines = []
    heading_seen = set()
    for heading in soup.find_all(["h1", "h2", "h3"])[:50]:
        value = clean_scalar(heading.get_text(" ", strip=True), 600)
        if value:
            append_unique(heading_lines, heading_seen, value)

    blocks = [
        clean_scalar(block, 3_000)
        for block in soup.get_text("\n", strip=True).splitlines()
    ]
    visible = [block for block in blocks if len(block) >= 20]

    sections = []
    if structured_lines:
        sections.append("Structured page data:\n" + "\n".join(structured_lines))
    if metadata_lines:
        sections.append("Page metadata:\n" + "\n".join(metadata_lines))
    if heading_lines:
        sections.append("Page headings:\n" + "\n".join(heading_lines))
    if table_sections:
        sections.append("Page tables:\n" + "\n\n".join(table_sections))
    if visible:
        sections.append("Visible page text:\n" + "\n".join(visible))
    return "\n\n".join(sections)[:MAX_OUTPUT_CHARS], published_at


def install_research_enhancements():
    """Install generic retrieval improvements without changing search intent or prompts."""
    if getattr(search.fetch_page, "_general_search_enhanced", False):
        return

    # search.fetch_page resolves extract_html from its module globals at call time.
    # Replacing it here upgrades both HTTP and browser-rendered page extraction.
    search.extract_html = extract_html
    original_fetch_page = search.fetch_page
    original_fetch_pages = search.fetch_pages

    def fetch_page_cached(url):
        if not search.public_url(url):
            return {"text": "", "error": "Blocked non-public or invalid URL"}

        cached = load_page(url)
        if cached:
            return cached

        try:
            result = original_fetch_page(url)
        except requests.RequestException:
            stale = load_page(url, allow_stale=True)
            if stale:
                return stale
            raise

        if result.get("text"):
            store_page(url, result)
        elif result.get("error") and not str(result["error"]).startswith("Blocked non-public"):
            stale = load_page(url, allow_stale=True)
            if stale:
                return stale
        return result

    fetch_page_cached._general_search_enhanced = True
    search.fetch_page = fetch_page_cached

    def fetch_pages_with_snippet_fallback(job_id, candidates, workers):
        results = original_fetch_pages(job_id, candidates, workers)
        for candidate in candidates:
            url = candidate.get("url")
            page = results.get(url, {})
            if not page.get("error"):
                continue
            snippet = clean_scalar(candidate.get("snippet"), 8_000)
            if len(snippet) < 80:
                continue
            results[url] = {
                "text": snippet,
                "url": url,
                "content_type": "text/search-snippet",
                "published_at": candidate.get("published_at", ""),
                "retrieval_note": f"Full page unavailable: {page.get('error')}",
            }
            search.event(
                job_id,
                "site",
                "partial",
                candidate.get("title") or url,
                "Full page unavailable; retaining the indexed search passage for relevance review",
                url,
            )
        return results

    fetch_pages_with_snippet_fallback._general_search_enhanced = True
    search.fetch_pages = fetch_pages_with_snippet_fallback
