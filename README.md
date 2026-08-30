# General Search

A general-purpose assistant extracted from Tender Designer. It can answer from local Ollama knowledge, plan targeted web research, read public pages, analyse uploaded documents, synthesise cited Markdown answers, keep conversations in browser storage, and export results as Markdown.

> **Maintenance note:** General Search and Tender Designer share this research engine. Whenever the engine is updated in either application, review and apply the equivalent upgrade to both applications so their search behaviour remains aligned.

## Run

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp settings.example.json settings.json
.venv/bin/python app.py
```

Open [http://127.0.0.1:5053](http://127.0.0.1:5053). Set your Ollama URL and model on the dedicated Settings page. The active port defaults to `5053`; override it with the `PORT` environment variable if needed.

## Notes

- Search conversations remain private in the browser's local storage.
- Use **Save chat .md** in the active conversation header to download the full conversation, including attachments and source links, as a shareable Markdown file.
- Chat uploads support PDF, DOCX, XLSX, CSV, TXT, Markdown, EML, and MSG. Extracted text is bounded and retained in browser-local conversation context for follow-up questions.
- Runtime settings are written to the ignored `settings.json` file.
- Search results are relevance-ranked and diversified before pages are opened. Pages are split into passages, and only the passages most relevant to the clarified request are retained.
- Research rounds assess evidence coverage and generate targeted follow-up searches for material gaps instead of stopping after the first usable page.
- Each retained source contributes a small evidence ledger of claims and supporting passages. A separate final pass checks inline citations against that ledger.
- Each fetched page is checked for query-specific usefulness before it is used. Useful domains are learned in the allowed list, but a single failed or irrelevant page no longer excludes an entire domain. Excluded domains remain under explicit user control.
- The app blocks loopback and private-network page fetching to reduce SSRF risk. Ollama itself may still be configured on a private address.
