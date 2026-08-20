from __future__ import annotations

import os
import json
from datetime import datetime, timezone
from io import BytesIO

from flask import Flask, jsonify, render_template, request, send_file

from document_extraction import clean_documents, document_context, extract_upload
from search import JOBS, list_models, start_job
from settings_store import PROMPTS, get_settings, save_prompts, save_settings


app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 30_000_000


@app.get("/")
def index():
    return render_template("index.html", settings=get_settings())


@app.get("/settings")
def settings_page():
    return render_template("settings.html", settings=get_settings(), prompts=PROMPTS.load())


@app.post("/api/search")
def search():
    if request.files:
        payload = request.form.to_dict()
        try:
            payload["history"] = json.loads(payload.get("history") or "[]")
            payload["documents"] = json.loads(payload.get("documents") or "[]")
        except json.JSONDecodeError:
            return jsonify(ok=False, message="The conversation document context was invalid."), 400
    else:
        payload = request.get_json(silent=True) or {}
    query = str(payload.get("query") or "").strip()[:20_000]
    if not query:
        return jsonify(ok=False, message="Enter a research question before searching."), 400
    history = []
    for item in (payload.get("history") or [])[-30:]:
        if isinstance(item, dict) and item.get("role") in {"user", "assistant"}:
            history.append({"role": item["role"], "content": str(item.get("content") or "")[:12_000]})
    documents = clean_documents(payload.get("documents") or [])
    for upload in request.files.getlist("documents"):
        if not upload.filename:
            continue
        try:
            documents.append(extract_upload(upload))
            documents = clean_documents(documents)
        except (ValueError, ImportError) as exc:
            return jsonify(ok=False, message=f"Could not process {upload.filename}: {exc}"), 400
        except Exception as exc:
            return jsonify(ok=False, message=f"Could not read {upload.filename}: {exc}"), 400
    allowed_only = str(payload.get("allowed_only") or "").lower() in {"1", "true", "yes", "on"}
    job = start_job(app, query, history, str(payload.get("model") or "")[:200], allowed_only, document_context(documents))
    return jsonify(ok=True, job=job, documents=documents, document_names=[item["name"] for item in documents]), 202


@app.get("/api/search/<job_id>")
def search_status(job_id: str):
    job = JOBS.get(job_id)
    if job is None:
        return jsonify(ok=False, message="Search job not found."), 404
    return jsonify(ok=True, job=job)


@app.get("/api/models")
def models():
    try:
        return jsonify(ok=True, models=list_models())
    except Exception as exc:
        configured = get_settings()["model"]
        return jsonify(ok=True, models=[configured] if configured else [], warning=str(exc))


@app.post("/api/settings")
def settings():
    payload = request.get_json(silent=True) or {}
    return jsonify(ok=True, settings=save_settings(payload), message="Search settings saved.")


@app.post("/api/prompts")
def prompts():
    payload = request.get_json(silent=True) or {}
    values = payload.get("prompts")
    if not isinstance(values, dict):
        return jsonify(ok=False, message="No prompts were supplied."), 400
    save_prompts(values)
    return jsonify(ok=True, message="Search instructions saved.")


@app.post("/api/export")
def export():
    payload = request.get_json(silent=True) or {}
    title = " ".join(str(payload.get("title") or "General Search Chat").split())[:120]
    messages = payload.get("messages")
    if isinstance(messages, list):
        messages = [item for item in messages[:100] if isinstance(item, dict) and item.get("role") in {"user", "assistant"} and str(item.get("content") or "").strip()]
        if not messages:
            return jsonify(ok=False, message="Start a chat before saving it."), 400
        lines = [f"# {title}", "", f"Saved: {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}"]
        document_names = [str(name).strip() for name in (payload.get("document_names") or []) if str(name).strip()][:50]
        if document_names:
            lines += ["", "**Documents in context:** " + ", ".join(document_names)]
        for item in messages:
            heading = "You" if item["role"] == "user" else "General Search"
            lines += ["", f"## {heading}", "", str(item.get("content") or "").strip()]
            attachments = [str(name).strip() for name in (item.get("attachments") or []) if str(name).strip()][:50]
            if attachments:
                lines += ["", "**Attachments:** " + ", ".join(attachments)]
            sources = [source for source in (item.get("sources") or []) if isinstance(source, dict) and source.get("url")][:50]
            if sources:
                lines += ["", "### Sources", ""]
                for index, source in enumerate(sources, 1):
                    lines.append(f"{index}. [{source.get('title') or source['url']}]({source['url']})")
    else:
        query = str(payload.get("query") or "").strip()
        answer = str(payload.get("answer") or "").strip()
        if not query or not answer:
            return jsonify(ok=False, message="Run a search before exporting."), 400
        lines = [f"# {title}", "", f"Generated: {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}", "", "## Research request", "", query, "", "## Answer", "", answer]
        sources = [source for source in (payload.get("sources") or []) if isinstance(source, dict) and source.get("url")][:50]
        if sources:
            lines += ["", "## Sources", ""]
            for index, source in enumerate(sources, 1):
                lines.append(f"{index}. [{source.get('title') or source['url']}]({source['url']})")
    content = ("\n".join(lines).strip() + "\n").encode()
    filename = "".join(character if character.isalnum() or character in " .-_" else "-" for character in title).strip(" .-")
    return send_file(BytesIO(content), mimetype="text/markdown", as_attachment=True, download_name=(filename or "general_search_result") + ".md")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5053")), debug=os.environ.get("FLASK_DEBUG") == "1")
