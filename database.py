"""SQLite persistence for interview sessions, questions and answers."""
import json
import os
import sqlite3

BASE_DIR = os.path.dirname(__file__)
DB_PATH = os.environ.get("DATABASE_PATH", os.path.join(BASE_DIR, "data", "interviews.db"))

# How long a connection waits for a lock before raising "database is locked",
# in milliseconds. Flask runs with threaded=True, so concurrent requests
# (e.g. the background next-question fetch overlapping an answer submission)
# can genuinely contend for the DB -- without this, SQLite's default is to
# fail immediately instead of waiting briefly for the other write to finish.
BUSY_TIMEOUT_MS = int(os.environ.get("DB_BUSY_TIMEOUT_MS", "5000"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    role            TEXT    NOT NULL,
    difficulty      TEXT    NOT NULL,
    personality     TEXT    NOT NULL,
    total_questions INTEGER NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'in_progress',
    overall_score   REAL,
    summary         TEXT,
    started_at      TEXT    NOT NULL,
    completed_at    TEXT
);
CREATE TABLE IF NOT EXISTS questions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id    INTEGER NOT NULL REFERENCES sessions(id),
    order_index   INTEGER NOT NULL,
    category      TEXT    NOT NULL,
    question_text TEXT    NOT NULL,
    hints         TEXT    NOT NULL DEFAULT '[]',
    asked_at      TEXT    NOT NULL
);
CREATE TABLE IF NOT EXISTS answers (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id         INTEGER NOT NULL REFERENCES questions(id),
    session_id          INTEGER NOT NULL REFERENCES sessions(id),
    answer_text         TEXT    NOT NULL,
    score               INTEGER NOT NULL,
    strengths           TEXT    NOT NULL,
    suggestions         TEXT    NOT NULL,
    improved_answer     TEXT    NOT NULL,
    filler_word_count   INTEGER NOT NULL DEFAULT 0,
    filler_words        TEXT    NOT NULL DEFAULT '[]',
    time_taken_seconds  INTEGER,
    answered_at         TEXT    NOT NULL
);

-- Every question/answer lookup filters by session_id (loading a session's
-- questions, building a report, listing history), and answer lookups by
-- question_id when checking whether a question's already been answered --
-- without these, each of those is a full table scan that gets slower as
-- more interviews are recorded.
CREATE INDEX IF NOT EXISTS idx_questions_session_id ON questions(session_id);
CREATE INDEX IF NOT EXISTS idx_answers_session_id ON answers(session_id);
CREATE INDEX IF NOT EXISTS idx_answers_question_id ON answers(question_id);
"""


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    # WAL lets readers and a writer proceed concurrently instead of blocking
    # each other, which matters here since the app does exactly that (e.g. a
    # background next-question fetch while the report page is being read).
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def _ensure_column(conn, table, column, ddl):
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with get_connection() as conn:
        conn.executescript(SCHEMA)
        _ensure_column(conn, "questions", "hints", "hints TEXT NOT NULL DEFAULT '[]'")
        conn.commit()


def row_to_dict(row):
    return dict(row)


def session_to_dict(row):
    d = row_to_dict(row)
    return d


def answer_to_dict(row):
    d = row_to_dict(row)
    d["strengths"] = json.loads(d["strengths"])
    d["suggestions"] = json.loads(d["suggestions"])
    d["filler_words"] = json.loads(d["filler_words"])
    return d


def question_to_dict(row):
    d = row_to_dict(row)
    d["hints"] = json.loads(d["hints"])
    return d