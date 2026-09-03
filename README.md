# General Search

A general-purpose assistant extracted from Tender Designer. It can answer from local Ollama knowledge, plan targeted web research, read public pages, analyse uploaded documents, synthesise cited Markdown answers, keep conversations in browser storage, and export results as Markdown.

General Search is the generic upstream research engine. Specialist applications should add their own domain strategy on top of it rather than embedding domain-specific behaviour into the core. For example, Internet Pricing can add pricing/procurement logic while Tender Designer can add specification and compliance logic, with both benefiting from the same retrieval, extraction, ranking and citation improvements.

> **Maintenance note:** General Search and Tender Designer share this research engine. Whenever the engine is updated in either application, review and apply the equivalent upgrade to both applications so their search behaviour remains aligned.

## Run

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
cp settings.example.json settings.json
.venv/bin/python app.py
```

On a new Ubuntu host, Playwright's system libraries may also need to be installed once with administrator privileges during provisioning:

```bash
sudo .venv/bin/python -m playwright install-deps chromium
```

For an existing deployment, `bash deploy/install-browser.sh` installs the compatible Chromium build. The script supports both a project-local `.venv` and the shared `../venv`; set `GENERAL_SEARCH_PYTHON` to override the interpreter. `PLAYWRIGHT_BROWSERS_PATH` can be used to share a browser installation between applications.

Open [http://127.0.0.1:5053](http://127.0.0.1:5053). Set your Ollama URL and model on the dedicated Settings page. The active port defaults to `5053`; override it with the `PORT` environment variable if needed.

## Generic research pipeline

General Search keeps the lightweight HTTP/PDF reader as the first choice. Search results are ranked and diversified, then shortlisted pages are fetched concurrently. Successful page reads are cached locally and reused, with stale cached content available as a fallback for temporary network failures.

HTML extraction now retains more than visible text. It also captures useful JSON-LD structured data, common metadata, headings and HTML tables before selecting query-relevant passages. This improves generic product research, technical specifications, software/service research, comparisons, ideas and other structured web content without making pricing the primary intent.

If a relevant HTML result cannot be read properly through the lightweight fetch, General Search can use bounded headless Chromium as a fallback. Chromium is used only when the page appears incomplete, when only a search-engine snippet was available, or when the fetched page does not contain the subject evidence suggested by the search result. PDFs continue through the lightweight reader.

The browser fallback:

- reuses one Chromium process and creates an isolated browser context for each page;
- renders at most three candidate pages per fetch batch by default;
- uses a 15 second navigation timeout by default;
- blocks images, media, fonts and common advertising/analytics hosts;
- validates browser requests and redirects as public URLs to retain SSRF protections;
- feeds rendered HTML through the same structured-data/table/text extractor;
- caches successful rendered content;
- does not attempt to bypass logins, CAPTCHAs, bot challenges or other access controls.

Browser and cache behaviour can be tuned with environment variables:

```bash
GENERAL_SEARCH_BROWSER_FALLBACK=1       # set 0 to disable
GENERAL_SEARCH_BROWSER_MAX_PAGES=3      # clamped to 0..5
GENERAL_SEARCH_BROWSER_TIMEOUT_MS=15000 # clamped to 5000..30000
GENERAL_SEARCH_BROWSER_SETTLE_MS=1500   # post-load settle, clamped to 0..5000
GENERAL_SEARCH_BROWSER_MIN_TEXT=700     # light-page text threshold
GENERAL_SEARCH_CACHE_DAYS=14            # fresh cache lifetime, clamped to 0..90
PLAYWRIGHT_BROWSERS_PATH=/path/to/playwright-browsers
```

## Notes

- Search conversations remain private in the browser's local storage.
- Use **Save chat .md** in the active conversation header to download the full conversation, including attachments and source links, as a shareable Markdown file.
- Chat uploads support PDF, DOCX, XLSX, CSV, TXT, Markdown, EML, and MSG. Extracted text is bounded and retained in browser-local conversation context for follow-up questions.
- Runtime settings are written to the ignored `settings.json` file.
- Retrieval cache data is stored under the ignored `instance/` directory.
- Search results are relevance-ranked and diversified before pages are opened. Pages are split into passages, and only the passages most relevant to the clarified request are retained.
- Page downloads run concurrently within a configurable worker limit. Redirect destinations are revalidated, response sizes are bounded, and public web PDFs can be read directly.
- Freshness-sensitive questions boost recent dated results. An optional Ollama embedding model can add semantic reranking while retaining a lexical fallback.
- Research rounds assess evidence coverage and generate targeted follow-up searches for material gaps instead of stopping after the first usable page.
- Each retained source contributes a small evidence ledger of claims and supporting passages. A separate final pass checks inline citations against that ledger.
- Each fetched page is checked for query-specific usefulness before it is used. Useful domains are learned in the allowed list, but a single failed or irrelevant page no longer excludes an entire domain. Excluded domains remain under explicit user control.
- The app blocks credentials in URLs and non-public network destinations, including every redirect hop, to reduce SSRF risk. Ollama itself may still be configured on a private address.
- Pricing, procurement, FX conversion, HV-equipment categories and budget-estimation rules are deliberately not part of the General Search core.
