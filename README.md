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
- Chat uploads support PDF, DOCX, XLSX, CSV, TXT, Markdown, EML, and MSG. Extracted text is bounded and retained in browser-local conversation context for follow-up questions.
- Runtime settings are written to the ignored `settings.json` file.
- Each fetched page is checked for query-specific usefulness before it is used. Useful domains are learned in the allowed list; failed, unreadable, or unusable domains are moved to the exclusion list. A domain is kept in only one list at a time.
- The app blocks loopback and private-network page fetching to reduce SSRF risk. Ollama itself may still be configured on a private address.
