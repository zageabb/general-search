# General Search

A standalone web-research chat extracted from Tender Designer. It plans targeted searches with a local Ollama model, reads public web pages, synthesises a cited Markdown answer, keeps conversations in browser storage, and exports results as Markdown.

> **Maintenance note:** General Search and Tender Designer share this research engine. Whenever the engine is updated in either application, review and apply the equivalent upgrade to both applications so their search behaviour remains aligned.

## Run

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp settings.example.json settings.json
.venv/bin/python app.py
```

Open [http://127.0.0.1:5053](http://127.0.0.1:5053). Set your Ollama URL and model in the app’s Settings panel. The active port defaults to `5053`; override it with the `PORT` environment variable if needed.

## Notes

- Search conversations remain private in the browser's local storage.
- Runtime settings are written to the ignored `settings.json` file.
- The app blocks loopback and private-network page fetching to reduce SSRF risk. Ollama itself may still be configured on a private address.
