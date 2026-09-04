"""Interview session orchestration: question sequencing, scoring, and the
final report, built on top of ai_analyzer (local TF-IDF scoring, no
external service) and database (SQLite)."""

import json
from datetime import datetime, timezone

import ai_analyzer
import database

ROLE_META = [
    {
        "id": "python-developer",
        "name": "Python Developer",
        "icon": "code-2",
        "description": "Backend logic, scripting, automation, and data pipelines",
        "topics": ["Python fundamentals", "OOP", "Data structures", "Decorators", "Generators", "Testing"],
    },
    {
        "id": "data-scientist",
        "name": "Data Scientist",
        "icon": "bar-chart-3",
        "description": "Statistics, machine learning, and data-driven insights",
        "topics": ["Statistics", "Pandas", "Machine learning", "Data cleaning", "Visualization", "Model evaluation"],
    },
    {
        "id": "web-developer",
        "name": "Web Developer",
        "icon": "globe",
        "description": "Frontend and backend web application development",
        "topics": ["HTML/CSS", "JavaScript", "REST APIs", "Responsive design", "Security", "Performance"],
    },
    {
        "id": "software-engineer",
        "name": "Software Engineer",
        "icon": "cpu",
        "description": "System design, algorithms, and engineering best practices",
        "topics": ["Data structures", "Algorithms", "System design", "Design patterns", "Concurrency", "Testing"],
    },
    {
        "id": "ai-engineer",
        "name": "AI Engineer",
        "icon": "brain-circuit",
        "description": "ML model deployment, NLP, and AI system architecture",
        "topics": ["Deep learning", "NLP", "Model deployment", "LLMs", "Vector databases", "MLOps"],
    },
    {
        "id": "devops-engineer",
        "name": "DevOps Engineer",
        "icon": "server",
        "description": "CI/CD, cloud infrastructure, and deployment automation",
        "topics": ["Docker", "Kubernetes", "CI/CD", "Cloud", "Monitoring", "Infrastructure as code"],
    },
]

DIFFICULTY_META = [
    {"id": "easy", "name": "Easy", "description": "Fundamental concepts and warm-up questions", "question_count": 4},
    {"id": "medium", "name": "Medium", "description": "Mixed technical and behavioral with moderate depth", "question_count": 5},
    {"id": "hard", "name": "Hard", "description": "Deep technical and problem-solving questions", "question_count": 6},
]

PERSONALITY_META = [
    {"id": "friendly", "name": "Friendly", "description": "Encouraging and supportive tone", "icon": "smile"},
    {"id": "professional", "name": "Professional", "description": "Balanced and business-like", "icon": "briefcase"},
    {"id": "strict", "name": "Strict", "description": "Rigorous and detail-oriented", "icon": "target"},
]

ROLES = [r["name"] for r in ROLE_META]
DIFFICULTIES = [d["id"] for d in DIFFICULTY_META]
PERSONALITIES = [p["id"] for p in PERSONALITY_META]

ROLE_BY_NAME = {r["name"]: r for r in ROLE_META}
QUESTIONS_BY_DIFFICULTY = {d["id"]: d["question_count"] for d in DIFFICULTY_META}

CATEGORY_CYCLE = ["technical", "behavioral", "technical", "problem-solving"]


def _now():
    return datetime.now(timezone.utc).isoformat()


def category_for_index(index):
    return CATEGORY_CYCLE[index % len(CATEGORY_CYCLE)]


def start_session(conn, role, difficulty, personality):
    if role not in ROLES:
        raise ValueError(f"Unknown role: {role}")
    if difficulty not in DIFFICULTIES:
        raise ValueError(f"Unknown difficulty: {difficulty}")
    if personality not in PERSONALITIES:
        raise ValueError(f"Unknown personality: {personality}")
    total_questions = QUESTIONS_BY_DIFFICULTY[difficulty]

    cursor = conn.execute(
        """
        INSERT INTO sessions (role, difficulty, personality, total_questions, status, started_at)
        VALUES (?, ?, ?, ?, 'in_progress', ?)
        """,
        (role, difficulty, personality, total_questions, _now()),
    )
    session_id = cursor.lastrowid
    conn.commit()

    question = _ask_next_question(conn, session_id, role, difficulty, personality, index=0)
    return session_id, question


def _ask_next_question(conn, session_id, role, difficulty, personality, index):
    category = category_for_index(index)

    previous = [
        r["question_text"]
        for r in conn.execute(
            "SELECT question_text FROM questions WHERE session_id = ? ORDER BY order_index",
            (session_id,),
        ).fetchall()
    ]
    topics = ROLE_BY_NAME.get(role, {}).get("topics")
    question_text = ai_analyzer.generate_question(role, difficulty, category, personality, previous, topics)
    hints_json = "[]"

    cursor = conn.execute(
        """
        INSERT INTO questions (session_id, order_index, category, question_text, hints, asked_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (session_id, index, category, question_text, hints_json, _now()),
    )
    conn.commit()

    return database.question_to_dict(
        conn.execute("SELECT * FROM questions WHERE id = ?", (cursor.lastrowid,)).fetchone()
    )


def get_session(conn, session_id):
    row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    return database.session_to_dict(row) if row else None


def get_current_question(conn, session_id):
    """The most recently asked question that has no answer yet."""
    row = conn.execute(
        """
        SELECT q.* FROM questions q
        LEFT JOIN answers a ON a.question_id = q.id
        WHERE q.session_id = ? AND a.id IS NULL
        ORDER BY q.order_index DESC LIMIT 1
        """,
        (session_id,),
    ).fetchone()
    return database.question_to_dict(row) if row else None


def get_hints(conn, session_id, question_id):
    """Return this question's hints, generating and caching them on first request
    so the up-front question-generation call stays fast."""
    row = conn.execute(
        "SELECT * FROM questions WHERE id = ? AND session_id = ?", (question_id, session_id)
    ).fetchone()
    if row is None:
        raise ValueError("Question not found")

    question = database.question_to_dict(row)
    if question["hints"]:
        return question["hints"]

    session = get_session(conn, session_id)
    hints = ai_analyzer.generate_hints(session["role"], question["question_text"], session["difficulty"])
    conn.execute("UPDATE questions SET hints = ? WHERE id = ?", (json.dumps(hints), question_id))
    conn.commit()
    return hints


def submit_answer(conn, session_id, answer_text, time_taken_seconds=None):
    session = get_session(conn, session_id)
    if session is None:
        raise ValueError("Session not found")
    if session["status"] != "in_progress":
        raise ValueError("Session is already completed")

    question = get_current_question(conn, session_id)
    if question is None:
        raise ValueError("No open question for this session")

    analysis = ai_analyzer.analyze_answer(session["role"], question["question_text"], answer_text, session["personality"])
    filler_count, filler_matches = ai_analyzer.count_filler_words(answer_text)

    conn.execute(
        """
        INSERT INTO answers
            (question_id, session_id, answer_text, score, strengths, suggestions,
             improved_answer, filler_word_count, filler_words, time_taken_seconds, answered_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            question["id"],
            session_id,
            answer_text,
            analysis["score"],
            json.dumps(analysis["strengths"]),
            json.dumps(analysis["suggestions"]),
            analysis["improved_answer"],
            filler_count,
            json.dumps(filler_matches),
            time_taken_seconds,
            _now(),
        ),
    )
    conn.commit()

    answered_count = question["order_index"] + 1
    result = {
        "question": question,
        "feedback": {**analysis, "filler_word_count": filler_count, "filler_words": filler_matches},
        "session_complete": False,
    }

    if answered_count >= session["total_questions"]:
        _complete_session(conn, session)
        result["session_complete"] = True

    return result


def get_or_create_next_question(conn, session_id):
    """Fetch the next unanswered question, generating it if it doesn't exist yet.
    Split out of submit_answer so answer feedback comes back fast; the client
    kicks this off separately (in the background, while the user reads their
    feedback) instead of bundling two AI calls into one request. Both calls
    are effectively instant with the local TF-IDF backend, but keeping
    them split still avoids compounding two calls' worst-case latency
    into a single response."""
    session = get_session(conn, session_id)
    if session is None:
        raise ValueError("Session not found")
    if session["status"] != "in_progress":
        raise ValueError("Session is already completed")

    existing = get_current_question(conn, session_id)
    if existing is not None:
        return existing

    index = conn.execute(
        "SELECT COUNT(*) AS c FROM questions WHERE session_id = ?", (session_id,)
    ).fetchone()["c"]
    if index >= session["total_questions"]:
        raise ValueError("No more questions for this session")

    return _ask_next_question(
        conn, session_id, session["role"], session["difficulty"], session["personality"], index
    )


def _complete_session(conn, session):
    """Mark the session finished and store its (fast, DB-only) overall score.
    The closing summary is deliberately NOT generated here -- it's the same
    class of extra AI call that get_or_create_next_question was already
    split out to avoid stacking onto a slow response. Generating it here
    would make finishing the interview slower than every other question
    (analyze_answer's call *plus* a summary call, back to back). Instead it's
    generated lazily on first report view via _ensure_summary, matching the
    pattern get_hints already uses for per-question hints."""
    answers = get_session_answers(conn, session["id"])
    overall_score = sum(a["score"] for a in answers) / len(answers) if answers else 0.0

    conn.execute(
        """
        UPDATE sessions
        SET status = 'completed', overall_score = ?, completed_at = ?
        WHERE id = ?
        """,
        (overall_score, _now(), session["id"]),
    )
    conn.commit()


def _ensure_summary(conn, session):
    """Generate and cache the closing summary on first access. Cheap on every
    call after the first -- returns the cached value straight from `session`
    without regenerating it again."""
    if session["summary"] is not None:
        return session["summary"]

    answers = get_session_answers(conn, session["id"])
    try:
        summary = ai_analyzer.generate_final_summary(
            session["role"],
            session["personality"],
            session["difficulty"],
            session["overall_score"] or 0.0,
            answers,
        )
    except ai_analyzer.OllamaError:
        summary = "Summary unavailable."

    conn.execute("UPDATE sessions SET summary = ? WHERE id = ?", (summary, session["id"]))
    conn.commit()
    session["summary"] = summary
    return summary


def get_session_answers(conn, session_id):
    rows = conn.execute(
        """
        SELECT a.*, q.question_text, q.category, q.order_index
        FROM answers a
        JOIN questions q ON q.id = a.question_id
        WHERE a.session_id = ?
        ORDER BY q.order_index
        """,
        (session_id,),
    ).fetchall()
    return [database.answer_to_dict(r) for r in rows]


GRADE_THRESHOLDS = [
    (85, "Outstanding", "emerald"),
    (70, "Well Done", "blue"),
    (55, "Good Effort", "amber"),
    (0, "Keep Practicing", "red"),
]


def grade_for_percentage(pct):
    for threshold, label, color in GRADE_THRESHOLDS:
        if pct >= threshold:
            return label, color
    return GRADE_THRESHOLDS[-1][1], GRADE_THRESHOLDS[-1][2]


def get_full_report(conn, session_id):
    session = get_session(conn, session_id)
    if session is None:
        return None
    if session["status"] == "completed":
        _ensure_summary(conn, session)

    answers = get_session_answers(conn, session_id)
    total_fillers = sum(a["filler_word_count"] for a in answers)

    percentage = None
    grade_label, grade_color = None, None
    best_answer = worst_answer = None
    if session["overall_score"] is not None:
        percentage = round(session["overall_score"] / 10 * 100)
        grade_label, grade_color = grade_for_percentage(percentage)
    if answers:
        best_answer = max(answers, key=lambda a: a["score"])
        worst_answer = min(answers, key=lambda a: a["score"])
        if worst_answer["id"] == best_answer["id"]:
            worst_answer = None

    return {
        "session": session,
        "answers": answers,
        "total_filler_words": total_fillers,
        "percentage": percentage,
        "grade_label": grade_label,
        "grade_color": grade_color,
        "best_answer": best_answer,
        "worst_answer": worst_answer,
    }


def list_sessions(conn):
    rows = conn.execute("SELECT * FROM sessions ORDER BY started_at DESC").fetchall()
    return [database.session_to_dict(r) for r in rows]


def delete_session(conn, session_id):
    conn.execute("DELETE FROM answers WHERE session_id = ?", (session_id,))
    conn.execute("DELETE FROM questions WHERE session_id = ?", (session_id,))
    conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    conn.commit()
