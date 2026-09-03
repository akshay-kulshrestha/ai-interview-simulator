import logging
import math
import os
import threading
from datetime import datetime, timezone

from flask import Flask, g, jsonify, render_template, request, send_file
from werkzeug.exceptions import HTTPException

import ai_analyzer
import database
import interview
import report_pdf

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True
log = logging.getLogger("interview_app")

DEFAULT_PORT = int(os.environ.get("PORT", "5001"))
FLASK_DEBUG = os.environ.get("FLASK_DEBUG", "0") == "1"

# Score-band thresholds and colors driving both the SVG ring gauge and any
# plain percentage badges. Defined once here so score_ring() and pct_color()
# can never drift out of sync with each other.
SCORE_BANDS = [
    (80, "#16a34a"),  # green  -- pct >= 80
    (60, "#2563eb"),  # blue   -- pct >= 60
    (40, "#f59e0b"),  # amber  -- pct >= 40
    (0, "#dc2626"),   # red    -- below 40
]

DEFAULT_RING_SIZE = int(os.environ.get("SCORE_RING_SIZE", "96"))
DEFAULT_RING_STROKE = int(os.environ.get("SCORE_RING_STROKE", "8"))


def pct_color(pct):
    for threshold, color in SCORE_BANDS:
        if pct >= threshold:
            return color
    return SCORE_BANDS[-1][1]


def score_ring(score, max_score=10, size=None, stroke=None):
    """Geometry + color for an SVG circular score gauge, matching the
    reference design's stroke-dashoffset ring (green/blue/amber/red bands)."""
    size = DEFAULT_RING_SIZE if size is None else size
    stroke = DEFAULT_RING_STROKE if stroke is None else stroke
    radius = (size - stroke) / 2
    circumference = 2 * math.pi * radius
    ratio = max(0, min(1, (score / max_score) if max_score else 0))
    pct = ratio * 100
    return {
        "size": size,
        "center": size / 2,
        "radius": radius,
        "stroke": stroke,
        "circumference": circumference,
        "offset": circumference * (1 - ratio),
        "color": pct_color(pct),
    }


def relative_time(iso_str):
    then = datetime.fromisoformat(iso_str)
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - then
    minutes = int(delta.total_seconds() // 60)
    hours = int(delta.total_seconds() // 3600)
    days = int(delta.total_seconds() // 86400)
    if minutes < 1:
        return "Just now"
    if minutes < 60:
        return f"{minutes}m ago"
    if hours < 24:
        return f"{hours}h ago"
    if days < 7:
        return f"{days}d ago"
    return f"{then.strftime('%b')} {then.day}, {then.year}"


@app.context_processor
def inject_globals():
    return {
        "role_meta": interview.ROLE_META,
        "difficulty_meta": interview.DIFFICULTY_META,
        "personality_meta": interview.PERSONALITY_META,
        "role_by_name": interview.ROLE_BY_NAME,
        "greetings": ai_analyzer.PERSONALITY_GREETINGS,
        "score_ring": score_ring,
        "pct_color": pct_color,
        "relative_time": relative_time,
    }


def get_db():
    if "db" not in g:
        g.db = database.get_connection()
    return g.db


@app.teardown_appcontext
def close_db(exception):
    db = g.pop("db", None)
    if db is not None:
        db.close()


@app.errorhandler(Exception)
def handle_unexpected_error(exc):
    """Never let an unhandled exception surface Flask's raw debug=False error
    page mid-demo. API calls get a JSON error the existing UI already renders
    nicely; page loads get a plain but on-brand message. Normal HTTP errors
    (404s, etc.) pass through untouched."""
    if isinstance(exc, HTTPException):
        return exc
    log.exception("Unhandled error on %s", request.path)
    if request.path.startswith("/api/"):
        return jsonify({"error": "Something went wrong on the server. Please try again."}), 500
    return (
        "<h1>Something went wrong</h1><p>Please go back and try again. "
        "If this keeps happening, check that Ollama is running.</p>",
        500,
    )


# ------------------------------------------------------------------- pages ---

@app.route("/")
def home():
    return render_template("setup.html", current_page="setup")


@app.route("/interview/<int:session_id>")
def interview_page(session_id):
    db = get_db()
    session = interview.get_session(db, session_id)
    if session is None:
        return "Interview session not found", 404
    if session["status"] == "completed":
        return render_template("interview.html", session=session, question=None, current_page="interview")
    question = interview.get_current_question(db, session_id)
    return render_template("interview.html", session=session, question=question, current_page="interview")


@app.route("/report/<int:session_id>")
def report_page(session_id):
    report = interview.get_full_report(get_db(), session_id)
    if report is None:
        return "Interview session not found", 404
    return render_template("report.html", report=report, current_page="report")


@app.route("/history")
def history_page():
    return render_template("history.html", sessions=interview.list_sessions(get_db()), current_page="history")


# --------------------------------------------------------------------- api ---

@app.route("/api/session/start", methods=["POST"])
def api_start_session():
    data = request.get_json(silent=True) or {}
    role = data.get("role")
    log.info("Starting session: role=%s difficulty=%s personality=%s", role, data.get("difficulty"), data.get("personality"))
    try:
        session_id, question = interview.start_session(
            get_db(),
            role=role,
            difficulty=data.get("difficulty"),
            personality=data.get("personality"),
        )
    except ValueError as exc:
        log.warning("Rejected session start: %s", exc)
        return jsonify({"error": str(exc)}), 400
    except ai_analyzer.OllamaError as exc:
        log.error("Ollama error starting session: %s", exc)
        return jsonify({"error": str(exc)}), 502

    log.info("Session %d started, first question ready", session_id)
    return jsonify({"session_id": session_id, "question": question}), 201


@app.route("/api/session/<int:session_id>/answer", methods=["POST"])
def api_submit_answer(session_id):
    data = request.get_json(silent=True) or {}
    answer_text = (data.get("answer") or "").strip()
    if not answer_text:
        return jsonify({"error": "Please provide an answer before submitting."}), 400

    log.info("Session %d: analyzing answer (%d chars)", session_id, len(answer_text))
    try:
        result = interview.submit_answer(
            get_db(), session_id, answer_text, data.get("time_taken_seconds")
        )
    except ValueError as exc:
        log.warning("Session %d: rejected answer submission: %s", session_id, exc)
        return jsonify({"error": str(exc)}), 400
    except ai_analyzer.OllamaError as exc:
        log.error("Session %d: Ollama error analyzing answer: %s", session_id, exc)
        return jsonify({"error": str(exc)}), 502

    log.info("Session %d: answer scored %d/10%s", session_id, result["feedback"]["score"], " (session complete)" if result["session_complete"] else "")
    return jsonify(result)


@app.route("/api/session/<int:session_id>/next-question", methods=["POST"])
def api_next_question(session_id):
    log.info("Session %d: fetching next question", session_id)
    try:
        question = interview.get_or_create_next_question(get_db(), session_id)
    except ValueError as exc:
        log.warning("Session %d: rejected next-question request: %s", session_id, exc)
        return jsonify({"error": str(exc)}), 400
    except ai_analyzer.OllamaError as exc:
        log.error("Session %d: Ollama error generating next question: %s", session_id, exc)
        return jsonify({"error": str(exc)}), 502
    log.info("Session %d: next question ready (Q%d)", session_id, question["order_index"] + 1)
    return jsonify({"question": question})


@app.route("/api/session/<int:session_id>/question/<int:question_id>/hints")
def api_question_hints(session_id, question_id):
    log.info("Session %d: fetching hints for question %d", session_id, question_id)
    try:
        hints = interview.get_hints(get_db(), session_id, question_id)
    except ValueError as exc:
        log.warning("Session %d: hints request rejected: %s", session_id, exc)
        return jsonify({"error": str(exc)}), 404
    except ai_analyzer.OllamaError as exc:
        log.error("Session %d: Ollama error generating hints: %s", session_id, exc)
        return jsonify({"error": str(exc)}), 502
    return jsonify({"hints": hints})


@app.route("/api/session/<int:session_id>/report")
def api_report(session_id):
    report = interview.get_full_report(get_db(), session_id)
    if report is None:
        return jsonify({"error": "Session not found"}), 404
    return jsonify(report)


@app.route("/report/<int:session_id>/pdf")
def download_report_pdf(session_id):
    report = interview.get_full_report(get_db(), session_id)
    if report is None:
        return "Interview session not found", 404
    if report["session"]["status"] != "completed":
        return "Interview isn't finished yet", 400

    file_path = report_pdf.generate_pdf(report)
    return send_file(file_path, as_attachment=True, download_name=os.path.basename(file_path))


@app.route("/api/warmup")
def api_warmup():
    """Hit this right before a demo to force-load the model into memory
    (and refresh its 30-minute keep-alive) even if the server has been idle."""
    ai_analyzer.warm_up()
    return jsonify({"ok": True})


@app.route("/api/history")
def api_history():
    return jsonify(interview.list_sessions(get_db()))


@app.route("/api/session/<int:session_id>", methods=["DELETE"])
def api_delete_session(session_id):
    db = get_db()
    if interview.get_session(db, session_id) is None:
        return jsonify({"error": "Session not found"}), 404
    interview.delete_session(db, session_id)
    return jsonify({"ok": True})


database.init_db()

# Load the model into memory in the background as soon as the server starts,
# so the founder's first click doesn't eat a slow cold-start.
threading.Thread(target=ai_analyzer.warm_up, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", debug=FLASK_DEBUG, port=DEFAULT_PORT, threaded=True)