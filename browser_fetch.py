from __future__ import annotations

import os
import queue
import threading
from concurrent.futures import Future, TimeoutError as FutureTimeoutError
from urllib.parse import urlparse

import search


BLOCKED_RESOURCE_TYPES = {"image", "media", "font"}
AD_HOST_TOKENS = (
    "doubleclick.", "googlesyndication.", "google-analytics.", "adservice.",
    "adnxs.", "facebook.net", "hotjar.", "scorecardresearch.",
)
GENERIC_QUERY_TERMS = {
    "find", "search", "look", "looking", "information", "details", "idea", "ideas",
    "product", "products", "best", "good", "current", "latest", "online", "website",
}


def _env_bool(name: str, default: bool = True) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off", "disabled"}


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def browser_fallback_enabled() -> bool:
    return _env_bool("GENERAL_SEARCH_BROWSER_FALLBACK", True)


def browser_page_limit() -> int:
    return _env_int("GENERAL_SEARCH_BROWSER_MAX_PAGES", 3, 0, 5)


def browser_timeout_ms() -> int:
    return _env_int("GENERAL_SEARCH_BROWSER_TIMEOUT_MS", 15_000, 5_000, 30_000)


def browser_settle_ms() -> int:
    return _env_int("GENERAL_SEARCH_BROWSER_SETTLE_MS", 1_500, 0, 5_000)


def browser_min_text_chars() -> int:
    return _env_int("GENERAL_SEARCH_BROWSER_MIN_TEXT", 700, 100, 5_000)


def candidate_is_relevant(candidate: dict) -> bool:
    query_terms = search.terms(candidate.get("query", "")) - GENERIC_QUERY_TERMS
    candidate_terms = search.terms(
        f"{candidate.get('title', '')} {candidate.get('snippet', '')}"
    )
    if query_terms & candidate_terms:
        return True
    return bool(str(candidate.get("snippet") or "").strip() and candidate.get("rank", 99) <= 3)


def should_render_candidate(candidate: dict, page: dict) -> bool:
    """Render only relevant HTML candidates where the lightweight reader is inadequate."""
    if not browser_fallback_enabled() or browser_page_limit() <= 0:
        return False
    url = str(candidate.get("url") or "")
    if not search.public_url(url):
        return False

    content_type = str(page.get("content_type") or "").lower()
    if "pdf" in content_type or "+rendered" in content_type:
        return False
    if str(page.get("error") or "").startswith("Blocked non-public"):
        return False
    if not candidate_is_relevant(candidate):
        return False

    page_text = str(page.get("text") or "")
    if page.get("error") or content_type == "text/search-snippet":
        return True
    if len(page_text.strip()) < browser_min_text_chars():
        return True

    anchors = search.terms(candidate.get("query", "")) - GENERIC_QUERY_TERMS
    candidate_overlap = anchors & search.terms(
        f"{candidate.get('title', '')} {candidate.get('snippet', '')}"
    )
    page_overlap = anchors & search.terms(page_text)
    return bool(candidate_overlap and not page_overlap)


class BrowserRenderer:
    """Own one Playwright browser on a dedicated thread and isolate each rendered page."""

    def __init__(self):
        self._queue: queue.Queue[tuple[str, int, Future]] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def render(self, url: str, timeout_ms: int) -> dict:
        self._ensure_worker()
        future: Future = Future()
        self._queue.put((url, timeout_ms, future))
        try:
            return future.result(timeout=(timeout_ms / 1000) + 12)
        except FutureTimeoutError:
            return {"text": "", "error": "Headless Chromium rendering exceeded its bounded timeout"}

    def _ensure_worker(self):
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._thread = threading.Thread(
                target=self._run, name="general-search-browser", daemon=True
            )
            self._thread.start()

    def _run(self):
        try:
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright
        except Exception as exc:
            self._fail_requests(f"Playwright is unavailable: {exc}")
            return

        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True, chromium_sandbox=True)
                while True:
                    url, timeout_ms, future = self._queue.get()
                    if future.cancelled():
                        continue
                    try:
                        if not browser.is_connected():
                            browser = playwright.chromium.launch(
                                headless=True, chromium_sandbox=True
                            )
                        result = self._render_one(
                            browser, url, timeout_ms, PlaywrightTimeoutError
                        )
                    except Exception as exc:
                        result = {
                            "text": "",
                            "error": f"Headless Chromium failed: {search.request_error(exc)}",
                        }
                    if not future.done():
                        future.set_result(result)
        except Exception as exc:
            self._fail_requests(
                f"Headless Chromium could not start: {search.request_error(exc)}"
            )

    def _fail_requests(self, message: str):
        while True:
            _url, _timeout_ms, future = self._queue.get()
            if not future.done():
                future.set_result({"text": "", "error": message})

    @staticmethod
    def _render_one(browser, url: str, timeout_ms: int, playwright_timeout_error) -> dict:
        if not search.public_url(url):
            return {"text": "", "error": "Blocked non-public or invalid URL"}

        context = browser.new_context(
            locale="en-GB",
            java_script_enabled=True,
            service_workers="block",
            accept_downloads=False,
        )
        page = context.new_page()

        def route_request(route):
            request = route.request
            if request.resource_type in BLOCKED_RESOURCE_TYPES:
                route.abort()
                return
            request_url = request.url
            parsed = urlparse(request_url)
            if parsed.scheme in {"data", "blob", "about"}:
                route.continue_()
                return
            host = (parsed.hostname or "").lower()
            if any(token in host for token in AD_HOST_TOKENS):
                route.abort()
                return
            if parsed.scheme not in {"http", "https"} or not search.public_url(request_url):
                route.abort()
                return
            route.continue_()

        try:
            context.route("**/*", route_request)
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            try:
                page.wait_for_function(
                    """() => {
                        const text = (document.body?.innerText || '').trim();
                        return text.length >= 300
                            || !!document.querySelector('main, article, table, [itemtype], h1');
                    }""",
                    timeout=min(3_000, max(500, timeout_ms // 4)),
                )
            except playwright_timeout_error:
                pass

            settle_ms = browser_settle_ms()
            if settle_ms:
                page.wait_for_timeout(settle_ms)

            final_url = page.url
            if not search.public_url(final_url):
                return {
                    "text": "",
                    "error": "Browser redirected to a non-public or invalid URL",
                }

            html = page.content()
            if len(html.encode("utf-8", errors="ignore")) > search.MAX_WEB_BYTES:
                return {
                    "text": "",
                    "error": (
                        f"Rendered page exceeds the "
                        f"{search.MAX_WEB_BYTES // 1_000_000} MB limit"
                    ),
                }

            text, published_at = search.extract_html(html.encode("utf-8"))
            return {
                "text": text,
                "url": final_url,
                "content_type": "text/html+rendered",
                "published_at": published_at,
            }
        finally:
            context.close()


_RENDERER = BrowserRenderer()
_ORIGINAL_FETCH_PAGES = None


def install_browser_fallback():
    """Add bounded Chromium rendering after the lightweight generic reader."""
    global _ORIGINAL_FETCH_PAGES
    if getattr(search.fetch_pages, "_playwright_fallback", False):
        return

    _ORIGINAL_FETCH_PAGES = search.fetch_pages

    def fetch_pages_with_browser(job_id, candidates, workers):
        results = _ORIGINAL_FETCH_PAGES(job_id, candidates, workers)
        remaining = browser_page_limit()
        if not browser_fallback_enabled() or remaining <= 0:
            return results

        for candidate in candidates:
            if remaining <= 0:
                break
            url = candidate.get("url")
            light_page = results.get(url, {})
            if not should_render_candidate(candidate, light_page):
                continue

            remaining -= 1
            search.event(
                job_id,
                "site",
                "initiated",
                candidate.get("title") or url,
                "Lightweight reading was incomplete; trying bounded headless Chromium rendering",
                url,
                "Rendering dynamic pages",
            )
            rendered = _RENDERER.render(url, browser_timeout_ms())
            if rendered.get("text"):
                results[url] = rendered
                try:
                    from retrieval_cache import store_page
                    store_page(url, rendered)
                except Exception:
                    pass
                search.event(
                    job_id,
                    "site",
                    "returned",
                    candidate.get("title") or url,
                    "Headless Chromium returned rendered page content",
                    url,
                )
            else:
                search.event(
                    job_id,
                    "site",
                    "partial",
                    candidate.get("title") or url,
                    rendered.get("error")
                    or "Headless Chromium returned no readable content",
                    url,
                )
        return results

    fetch_pages_with_browser._playwright_fallback = True
    search.fetch_pages = fetch_pages_with_browser
