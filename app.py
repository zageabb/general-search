from __future__ import annotations

import os
from datetime import datetime, timezone
from io import BytesIO

from flask import Flask, jsonify, render_template, request, send_file

from search import JOBS, list_models, start_job
from settings_store import PROMPTS, get_settings, save_prompts, save_settings


app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 1_000_000


@app.get("/")
def index():
    return render_template("index.html", settings=get_settings(), prompts=PROMPTS.load())


@app.post("/api/search")
def search():
    payload = request.get_json(silent=True) or {}
    query = str(payload.get("query") or "").strip()[:20_000]
    if not query:
        return jsonify(ok=False, message="Enter a research question before searching."), 400
    history = []
    for item in (payload.get("history") or [])[-30:]:
        if isinstance(item, dict) and item.get("role") in {"user", "assistant"}:
            history.append({"role": item["role"], "content": str(item.get("content") or "")[:12_000]})
    job = start_job(app, query, history, str(payload.get("model") or "")[:200], bool(payload.get("allowed_only")))
    return jsonify(ok=True, job=job), 202


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
    query = str(payload.get("query") or "").strip()
    answer = str(payload.get("answer") or "").strip()
    if not query or not answer:
        return jsonify(ok=False, message="Run a search before exporting."), 400
    lines = ["# General Search Result", "", f"Generated: {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}", "", "## Research request", "", query, "", "## Answer", "", answer]
    sources = payload.get("sources") or []
    if sources:
        lines += ["", "## Sources", ""]
        for index, source in enumerate(sources[:50], 1):
            lines.append(f"{index}. [{source.get('title') or source.get('url')}]({source.get('url')})")
    content = ("\n".join(lines).strip() + "\n").encode()
    return send_file(BytesIO(content), mimetype="text/markdown", as_attachment=True, download_name="general_search_result.md")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5053")), debug=os.environ.get("FLASK_DEBUG") == "1")
