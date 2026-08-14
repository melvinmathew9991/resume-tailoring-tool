"""
app.py -- Flask API for the resume-editing tool.

Endpoints:
  GET  /api/health              -- liveness check
  GET  /api/projects            -- list all projects in the bank
  POST /api/match                -- score projects against a pasted JD
  POST /api/generate             -- build + compile a resume PDF from selected projects

Deliberately thin: all real logic lives in matcher.py / resume_builder.py /
pdf_compiler.py, which are independently unit-tested. This file's job is
HTTP plumbing and input validation, not business logic.
"""
import base64
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from flask import Flask, request, jsonify
from flask_cors import CORS

from matcher import match_jd_to_bank, find_gap_terms
from latex_utils import latex_to_display_text
from resume_builder import build_resume, load_project_bank, PERSONAL_INFO, DEFAULT_SUMMARY
from pdf_compiler import CompilationError, LatexNotFoundError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("resume-tool-api")

app = Flask(__name__)
CORS(app)  # local dev tool; tighten this before any real deployment

_BANK_CACHE = None


def get_bank():
    global _BANK_CACHE
    if _BANK_CACHE is None:
        _BANK_CACHE = load_project_bank()
    return _BANK_CACHE


@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Not found"}), 404


@app.errorhandler(500)
def server_error(e):
    logger.exception("Unhandled server error")
    return jsonify({"error": "Internal server error. Check server logs."}), 500


@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/api/projects", methods=["GET"])
def list_projects():
    bank = get_bank()
    return jsonify({
        "projects": [
            {"key": key, "title": latex_to_display_text(p["title"]), "domain": p.get("domain", [])}
            for key, p in bank.items()
            if key != "transformer_scratch"
        ]
    })


@app.route("/api/match", methods=["POST"])
def match():
    data = request.get_json(silent=True)
    if not data or "jd_text" not in data:
        return jsonify({"error": "Request body must be JSON with a 'jd_text' field."}), 400

    jd_text = data["jd_text"]
    if not isinstance(jd_text, str) or not jd_text.strip():
        return jsonify({"error": "'jd_text' must be a non-empty string."}), 400

    bank = get_bank()
    results = match_jd_to_bank(jd_text, bank, exclude_keys={"transformer_scratch"})
    gaps = find_gap_terms(jd_text, bank)

    return jsonify({
        "ranked_projects": [
            {
                "key": r.key,
                "title": latex_to_display_text(r.title),
                "score": r.score,
                "matched_keywords": r.matched_keywords,
                "domain_match": r.domain_match,
            }
            for r in results
        ],
        "gap_terms": gaps[:30],  # cap to avoid an unbounded response on a huge JD paste
        "note": (
            "This is a keyword pre-scan, not a final recommendation. "
            "A zero-match project is not necessarily irrelevant -- it may "
            "just use different vocabulary than this JD. Apply judgment "
            "before excluding a strong project on score alone."
        ),
    })


@app.route("/api/generate", methods=["POST"])
def generate():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be JSON."}), 400

    selected_keys = data.get("selected_project_keys")
    if not isinstance(selected_keys, list):
        return jsonify({"error": "'selected_project_keys' must be a list of strings."}), 400

    max_pages = data.get("max_pages", 2)
    if not isinstance(max_pages, int) or max_pages < 1:
        return jsonify({"error": "'max_pages' must be a positive integer."}), 400

    summary = data.get("summary")  # optional override
    personal_info = data.get("personal_info")  # optional override dict

    bank = get_bank()

    try:
        result = build_resume(
            selected_keys,
            bank=bank,
            summary=summary,
            personal_info=personal_info,
            max_pages=max_pages,
        )
    except KeyError as e:
        return jsonify({"error": str(e)}), 400
    except LatexNotFoundError as e:
        return jsonify({"error": f"Server misconfiguration: {e}"}), 500
    except CompilationError as e:
        return jsonify({
            "error": str(e),
            "log_tail": e.log_tail,
        }), 422

    return jsonify({
        "pdf_base64": base64.b64encode(result.pdf_bytes).decode("ascii"),
        "page_count": result.page_count,
        "font_size_used": result.font_size_used,
        "compile_attempts": result.attempts,
        "warning": result.warning,
    })


if __name__ == "__main__":
    # debug=False deliberately: Flask's debug reloader spawns a child
    # process, which complicates process management for anyone running
    # this via a process manager / background job. Set debug=True only
    # for interactive local development.
    app.run(debug=False, port=5001)
