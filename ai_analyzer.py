"""Anthropic-backed question generation and answer analysis.

Talks to the Anthropic API (https://docs.claude.com) over HTTPS. Requires
the ANTHROPIC_API_KEY environment variable to be set before any of this
will work.

This module previously talked to a locally running Ollama server -- that
constrained the app to running only on the machine it's demoed on, since
no free cloud host runs Ollama for you (and even if one did, a local LLM
needs far more RAM than a free-tier instance provides). Switching to a
cloud API is what makes this app deployable to something like Render.

The JSON-parsing/repair helpers below (_extract_json,
_attempt_close_truncated_json, etc.) were written to work around specific
failure modes of a small local model (llama3.2) run on CPU -- truncated
output from a token cap, occasionally malformed JSON, echoing the
candidate's answer back as the "improvement". Claude is meaningfully more
reliable at following "respond with strict JSON only" instructions, so in
practice these should fire far less often -- but they're left in place as
a defensive safety net rather than removed, since they cost nothing when
unused and still protect against a truncated/malformed response under
this backend too (e.g. from a transient API hiccup).
"""

import json
import logging
import os
import re
import time

import anthropic

log = logging.getLogger("interview_app")

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
# Haiku is the default here rather than a larger model: this app makes many
# small, structured calls per session (a question, then hints, then a score
# + feedback, repeated per question, plus a final summary) rather than a
# few large ones, so per-call cost and latency matter more than squeezing
# out extra reasoning quality on a task this constrained (a 0-10 score and
# a few short JSON fields). Override via env var to use a larger model.
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
REQUEST_TIMEOUT = int(os.environ.get("ANTHROPIC_REQUEST_TIMEOUT", "90"))

_client = None


def _get_client():
    """Lazily construct the Anthropic client so a missing API key fails
    with a clear error on first real use rather than crashing at import
    time (matters for warm_up(), which is called in a background thread
    at startup and must not take the whole app down if misconfigured)."""
    global _client
    if _client is None:
        if not ANTHROPIC_API_KEY:
            raise OllamaError(
                "ANTHROPIC_API_KEY is not set. Set it in your environment "
                "(or .env locally) before starting the app."
            )
        _client = anthropic.Anthropic(
            api_key=ANTHROPIC_API_KEY,
            timeout=REQUEST_TIMEOUT,
        )
    return _client

# Output-length caps per call, passed through as each API call's max_tokens.
# These matter less for latency with a cloud API than they did on local CPU
# hardware, but they still cap cost per call and give _extract_json's
# truncation-repair path a concrete ceiling to reason about.
# ANALYSIS_NUM_PREDICT must be large enough to fit the *entire* JSON object
# (score + strengths + suggestions + improved_answer) -- if the model hits
# this cap mid-string, the JSON is truncated/invalid and _generate_json has
# to retry the whole call, which costs more time than a higher cap would.
WARMUP_NUM_PREDICT = int(os.environ.get("WARMUP_NUM_PREDICT", "10"))
QUESTION_NUM_PREDICT = int(os.environ.get("QUESTION_NUM_PREDICT", "90"))
HINTS_NUM_PREDICT = int(os.environ.get("HINTS_NUM_PREDICT", "120"))
ANALYSIS_NUM_PREDICT = int(os.environ.get("ANALYSIS_NUM_PREDICT", "450"))
SUMMARY_NUM_PREDICT = int(os.environ.get("SUMMARY_NUM_PREDICT", "220"))

QUESTION_GENERATION_ATTEMPTS = int(os.environ.get("QUESTION_GENERATION_ATTEMPTS", "3"))
HINTS_GENERATION_ATTEMPTS = int(os.environ.get("HINTS_GENERATION_ATTEMPTS", "2"))
MAX_HINTS = int(os.environ.get("MAX_HINTS", "3"))
MAX_STRENGTHS = int(os.environ.get("MAX_STRENGTHS", "6"))
MAX_SUGGESTIONS = int(os.environ.get("MAX_SUGGESTIONS", "6"))
MIN_SCORE = 0
MAX_SCORE = 10
MIN_ANSWER_WORDS = int(os.environ.get("MIN_ANSWER_WORDS", "5"))
# Kept comfortably under ANALYSIS_NUM_PREDICT's budget once JSON structure,
# strengths, and suggestions are accounted for.
IMPROVED_ANSWER_MAX_WORDS = int(os.environ.get("IMPROVED_ANSWER_MAX_WORDS", "70"))
MIN_IMPROVED_ANSWER_WORDS = int(os.environ.get("MIN_IMPROVED_ANSWER_WORDS", "8"))
# How many strengths/suggestions to actually ask the model for. Lower than
# MAX_STRENGTHS/MAX_SUGGESTIONS (which just cap however many come back) --
# asking for fewer up front reduces total output length, which is the main
# lever against chronic truncation on slow local hardware.
PROMPT_LIST_ITEM_COUNT = int(os.environ.get("PROMPT_LIST_ITEM_COUNT", "2"))

PERSONALITY_PROMPTS = {
    "friendly": (
        "You are a warm, encouraging interviewer. You put candidates at ease, "
        "phrase questions approachably, and give supportive, constructive feedback."
    ),
    "professional": (
        "You are a balanced, business-like interviewer. You are courteous and neutral, "
        "ask clear well-scoped questions, and give even-handed, structured feedback."
    ),
    "strict": (
        "You are a rigorous, no-nonsense technical interviewer. You expect precise, "
        "well-justified answers, ask pointed questions, and give blunt, high-standard feedback."
    ),
}

PERSONALITY_GREETINGS = {
    "friendly": "Welcome! I'm excited to learn about your experience. Take your time — this is a safe space to practice.",
    "professional": "Welcome to the interview. I will ask a series of questions. Please provide clear, structured answers.",
    "strict": "Welcome. I expect precise, well-structured answers. Pay attention to detail and cover all aspects of each question.",
}

FILLER_WORDS = [
    "um", "uh", "erm", "hmm", "like", "you know", "sort of", "kind of",
    "basically", "actually", "literally", "i mean", "so yeah", "right",
]


class OllamaError(RuntimeError):
    """Raised when the AI backend can't be reached or returns bad output.

    Kept under its original name (from when this module talked to a local
    Ollama server) for compatibility with existing `except
    ai_analyzer.OllamaError` handlers in app.py and interview.py -- renaming
    it would mean touching both of those files for no functional benefit.
    """


def _anthropic_generate(prompt, system, num_predict=200, format_schema=None):
    """format_schema: optional JSON Schema describing the required output
    shape. When given, this is enforced via Claude's tool-use with a forced
    tool choice -- the model must call a "submit_result" tool whose
    arguments are grammar-constrained to the schema, so (for example) it's
    structurally impossible for it to emit a bare, unquoted -1 in place of
    a real string for "improved_answer" (an actual observed failure under
    the previous backend). When no schema is given, the call instead relies
    on system-prompt wording ("respond with strict JSON only") the same way
    the previous Ollama "format": "json" mode did, and the response text is
    parsed by _extract_json exactly as before."""
    start = time.monotonic()
    client = _get_client()

    try:
        if format_schema is not None:
            response = client.messages.create(
                model=ANTHROPIC_MODEL,
                max_tokens=max(num_predict, 16),
                system=system,
                messages=[{"role": "user", "content": prompt}],
                tools=[{
                    "name": "submit_result",
                    "description": "Submit the structured result for this request.",
                    "input_schema": format_schema,
                }],
                tool_choice={"type": "tool", "name": "submit_result"},
            )

            tool_use = next(
                (block for block in response.content if block.type == "tool_use"),
                None,
            )

            if tool_use is None:
                raise OllamaError(
                    "Model response did not include the expected tool call."
                )

            raw_text = json.dumps(tool_use.input)

        else:
            response = client.messages.create(
                model=ANTHROPIC_MODEL,
                max_tokens=max(num_predict, 16),
                system=f"{system} Respond with strict JSON only, no other text.",
                messages=[{"role": "user", "content": prompt}],
            )

            raw_text = "".join(
                block.text for block in response.content if block.type == "text"
            )

    except anthropic.APIConnectionError as exc:
        log.error("Anthropic API unreachable: %s", exc)
        raise OllamaError(
            "Couldn't reach the Anthropic API. Check your network connection "
            "and that ANTHROPIC_API_KEY is set correctly."
        ) from exc
    except anthropic.AuthenticationError as exc:
        log.error("Anthropic API authentication failed: %s", exc)
        raise OllamaError(
            "Anthropic API authentication failed. Check that ANTHROPIC_API_KEY "
            "is set to a valid key."
        ) from exc
    except anthropic.APIError as exc:
        log.error("Anthropic API request failed: %s", exc)
        raise OllamaError(f"Anthropic API request failed: {exc}") from exc

    elapsed = time.monotonic() - start
    log.info("Anthropic call done in %.1fs (max_tokens=%d)", elapsed, num_predict)
    return raw_text


def _extract_json(raw_text):
    """Find and parse the first complete top-level JSON object in raw_text.

    This was written against a small local model's quirks (occasionally
    appending extra malformed text/blobs after a perfectly valid object, or
    getting cut off mid-object by the token cap) and is far less likely to
    be needed against Claude -- but it's kept as a defensive fallback, since
    a naive greedy '{.*}' regex would still break the same two ways if it
    ever did happen: it breaks on trailing garbage (spans into it and fails
    to parse) and can't help with mid-object truncation. Scanning for the
    first object's *matching* closing brace (respecting string/escape
    context) handles both."""
    brace_start = raw_text.find("{")
    if brace_start == -1:
        raise OllamaError(f"Model did not return parseable JSON: {raw_text[:200]!r}")

    end = _find_matching_brace(raw_text, brace_start)
    if end is not None:
        candidate = raw_text[brace_start:end + 1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass  # fall through to truncation-repair attempt below

    repaired = _attempt_close_truncated_json(raw_text[brace_start:])
    if repaired is not None:
        # Log the actual raw text here, not just that a repair happened --
        # without this, a successful-but-lossy repair (e.g. a field ending up
        # with a short garbage value) is invisible: we only ever see the
        # clean, already-repaired result afterward, never what the model
        # actually produced. Truncated to keep log lines readable.
        log.warning(
            "Repaired a truncated JSON response (likely hit num_predict cap). "
            "Raw model output was: %r",
            raw_text[:500],
        )
        return repaired

    raise OllamaError(f"Model did not return parseable JSON: {raw_text[:200]!r}")


def _find_matching_brace(text, start):
    """Return the index of the '}' that closes the '{' at `start`, tracking
    string/escape context so braces inside string values don't confuse the
    count. Returns None if the object never closes (truncated)."""
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
    return None


def _attempt_close_truncated_json(fragment):
    """Best-effort repair for JSON cut off mid-string/mid-array/mid-object by
    hitting the num_predict token cap. Trims back to the last safely-closable
    point and appends the closing punctuation needed to make it valid.
    Returns None if it can't produce something parseable."""
    trimmed = fragment.rstrip()
    # If we're mid-string (odd number of unescaped quotes), back off to the
    # last complete, comma-terminated element before the dangling one.
    if trimmed.count('"') % 2 == 1:
        cutoff = trimmed.rfind(",")
        if cutoff == -1:
            return None
        trimmed = trimmed[:cutoff]

    opens = [c for c in trimmed if c in "{["]
    closes = {"{": "}", "[": "]"}
    stack = []
    for ch in trimmed:
        if ch in "{[":
            stack.append(ch)
        elif ch in "}]":
            if stack:
                stack.pop()
    closing = "".join(closes[ch] for ch in reversed(stack))

    for candidate in (trimmed + closing, trimmed.rstrip(",") + closing):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return None


def _generate_json(prompt, system, num_predict, format_schema=None):
    """Call the model and parse its JSON response, retrying once on a bad
    response — local models occasionally produce a malformed reply, and a
    single retry meaningfully improves reliability without adding much delay."""
    try:
        return _extract_json(_anthropic_generate(prompt, system, num_predict, format_schema))
    except OllamaError as exc:
        log.warning("Retrying after bad response: %s", exc)
        return _extract_json(_anthropic_generate(prompt, system, num_predict, format_schema))


def warm_up():
    """Send a trivial request at startup to fail fast on misconfiguration
    (missing/invalid ANTHROPIC_API_KEY, no network) rather than surfacing
    that as a confusing error on a candidate's first real interview
    question. There's no local model to "load" with a cloud API, but this
    is still worth calling once at app startup (in a background thread) --
    it catches setup problems immediately instead of mid-demo."""
    log.info("Verifying Anthropic API connectivity (model=%s)...", ANTHROPIC_MODEL)
    try:
        _anthropic_generate(
            "Reply with {\"ok\": true}",
            "Respond with strict JSON only.",
            num_predict=WARMUP_NUM_PREDICT,
        )
        log.info("Anthropic API reachable and ready.")
    except OllamaError as exc:
        log.warning("Warm-up failed (will retry on first real request): %s", exc)


CATEGORY_KIND = {
    "technical": "a technical",
    "behavioral": "a behavioral/HR",
    "problem-solving": "a hands-on problem-solving/coding",
}


def generate_question(role, difficulty, category, personality, previous_questions, topics=None):
    system = (
        f"{PERSONALITY_PROMPTS[personality]} You are interviewing a candidate for a "
        f"{role} position. Respond with strict JSON only: {{\"question\": \"...\"}}."
    )
    avoid = ""
    if previous_questions:
        avoid = "Do not repeat or closely resemble any of these already-asked questions:\n" + "\n".join(
            f"- {q}" for q in previous_questions
        )

    kind = CATEGORY_KIND.get(category, "a technical")
    topic_hint = f" Focus on topics such as: {', '.join(topics)}." if topics else ""
    prompt = (
        f"Ask {kind} interview question for a {role} role at {difficulty} difficulty.{topic_hint} "
        "Ask exactly one question, no preamble, no numbering.\n" + avoid
    )

    # Kept short (no hints here) so starting a session / advancing to the next
    # question comes back quickly; hints are generated lazily on demand.
    # Single retry budget of 3 raw calls total, covering both unparseable JSON
    # and valid-but-empty JSON (e.g. {"question": ""}) in one loop -- stacking
    # _generate_json's own internal retry on top of an outer retry here would
    # allow up to 6 sequential API calls in the worst case -- each one is
    # fast against Claude, but this still bounds the total worst-case
    # latency and cost for generating a single question.
    log.info("Generating %s question: role=%s difficulty=%s category=%s", "starter" if not previous_questions else "follow-up", role, difficulty, category)
    for attempt in range(QUESTION_GENERATION_ATTEMPTS):
        try:
            data = _extract_json(_anthropic_generate(prompt, system, num_predict=QUESTION_NUM_PREDICT))
        except OllamaError as exc:
            log.warning(
                "Question generation attempt %d/%d failed to parse: %s",
                attempt + 1, QUESTION_GENERATION_ATTEMPTS, exc,
            )
            continue
        question = _coerce_question(data)
        if question:
            log.info(
                "Question generated (attempt %d/%d): %s",
                attempt + 1, QUESTION_GENERATION_ATTEMPTS, question[:80],
            )
            return question
        log.warning(
            "Question generation attempt %d/%d returned no usable question",
            attempt + 1, QUESTION_GENERATION_ATTEMPTS,
        )

    # All attempts failed to produce usable JSON -- rather than surfacing a
    # 502 mid-interview (which breaks the session), fall back to a fixed
    # generic question for the category so the candidate can keep going.
    fallback = _fallback_question(category, previous_questions)
    log.error(
        "Question generation failed after %d attempts -- using fallback question",
        QUESTION_GENERATION_ATTEMPTS,
    )
    return fallback


FALLBACK_QUESTIONS = {
    "technical": [
        "What Python data structure would you use to remove duplicate items from a list, and why?",
        "How do you handle exceptions in Python, and when would you use a custom exception class?",
    ],
    "behavioral": [
        "Tell me about a time you had to learn a new technology quickly for a project.",
        "Describe a situation where you disagreed with a teammate's technical decision. How did you handle it?",
    ],
    "problem-solving": [
        "How would you find the second-largest number in an unsorted list without sorting it?",
        "Walk me through how you'd debug a function that's returning incorrect results intermittently.",
    ],
}


def _fallback_question(category, previous_questions):
    """Pick a fixed question for the category that hasn't already been asked
    this session, used when live generation fails repeatedly."""
    pool = FALLBACK_QUESTIONS.get(category) or FALLBACK_QUESTIONS["technical"]
    asked = set(previous_questions or [])
    for question in pool:
        if question not in asked:
            return question
    return pool[0]  # every fallback already asked (unlikely) -- reuse the first


def _coerce_question(data):
    """Usually the model returns {"question": "..."}, but this small model
    sometimes flips the shape and puts the question text in the JSON key
    instead of the value (e.g. {"Can you describe...?": "..."}) -- recover
    the question from that shape rather than treating it as empty."""
    question = str(data.get("question", "")).strip()
    if question:
        return question
    if isinstance(data, dict) and len(data) == 1:
        key = next(iter(data))
        if isinstance(key, str) and key.strip():
            return key.strip().rstrip("}").strip()
    return ""


def generate_hints(role, question_text, difficulty):
    system = (
        f"You are a helpful interview coach for a {role} position. Respond with strict "
        f'JSON only: {{"hints": ["...", "...", "..."]}}. Exactly {MAX_HINTS} short, non-spoiling '
        "nudges (a few words each) that point the candidate toward a strong answer without "
        "giving it away. The list must not be empty."
    )
    prompt = f"Interview question ({difficulty} difficulty): {question_text}"

    log.info("Generating hints for: %s", question_text[:80])
    # _generate_json's own retry only covers unparseable JSON -- it won't retry
    # on valid-but-empty output like {"hints": []}, which the model sometimes
    # returns even when parsing succeeds cleanly. Retry that case explicitly.
    for attempt in range(HINTS_GENERATION_ATTEMPTS):
        try:
            data = _generate_json(prompt, system, num_predict=HINTS_NUM_PREDICT)
        except OllamaError as exc:
            log.warning(
                "Hints generation attempt %d/%d failed: %s",
                attempt + 1, HINTS_GENERATION_ATTEMPTS, exc,
            )
            continue
        hints = [str(h).strip() for h in (data.get("hints") or []) if str(h).strip()][:MAX_HINTS]
        if hints:
            log.info("Generated %d hint(s) (attempt %d/%d)", len(hints), attempt + 1, HINTS_GENERATION_ATTEMPTS)
            return hints
        log.warning(
            "Hints generation attempt %d/%d returned an empty list",
            attempt + 1, HINTS_GENERATION_ATTEMPTS,
        )

    log.warning("Hints generation failed after %d attempts -- using generic fallback hints", HINTS_GENERATION_ATTEMPTS)
    return list(GENERIC_FALLBACK_HINTS[:MAX_HINTS])


GENERIC_FALLBACK_HINTS = [
    "Structure your answer with a clear beginning, middle, and end.",
    "Back up your points with a concrete example from experience if you can.",
    "Mention any trade-offs or edge cases relevant to your answer.",
]


# Structured-output schema for analyze_answer, enforced via Claude's
# tool-use (see _anthropic_generate) rather than relying on prompt wording
# alone. This grammar-constrains each field to its declared type at
# generation time -- e.g. it makes it structurally impossible for the model
# to emit a bare, unquoted -1 in place of a real string for
# "improved_answer" (an actual observed failure under the previous local-
# model backend), since the schema requires a string token there, not just
# "valid JSON somewhere".
ANALYSIS_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "integer"},
        "improved_answer": {"type": "string"},
        "strengths": {"type": "array", "items": {"type": "string"}},
        "suggestions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["score", "improved_answer", "strengths", "suggestions"],
}


def analyze_answer(role, question, answer, personality):
    # Small local models are prone to hallucinating plausible-sounding,
    # question-relevant feedback even for near-empty input (e.g. scoring a
    # literal "answer" placeholder 8/10 with specific praise) -- rather than
    # trust the model to recognize a non-answer, catch it deterministically
    # before spending an API call on something with nothing to evaluate.
    word_count = len(re.findall(r"[A-Za-z0-9]+", answer))
    if word_count < MIN_ANSWER_WORDS:
        log.info(
            "Answer too short (%d words, need %d) -- skipping API call for: %s",
            word_count, MIN_ANSWER_WORDS, question[:80],
        )
        return {
            "score": MIN_SCORE,
            "strengths": [],
            "suggestions": [
                "This answer is too short to evaluate -- please write a "
                "complete response that directly addresses the question."
            ],
            "improved_answer": "",
        }

    system = (
        f"{PERSONALITY_PROMPTS[personality]} You are grading a candidate's interview "
        f"answer for a {role} position. Respond with strict JSON only, matching this shape "
        # Field order matters under a token budget: whichever field generates last is the
        # one that gets corrupted/cut off if the model runs out of room. "score" goes first
        # since a wrong/missing score is the most visibly wrong thing to show; "improved_answer"
        # goes second since it turns into garbage (e.g. a stray "-1.5") if truncated, unlike
        # strengths/suggestions which degrade gracefully (UI just shows fewer bullets, or
        # hides the block if empty).
        "and FIELD ORDER (score first, then improved_answer, then strengths, then "
        "suggestions): "
        '{"score": <integer 0-10>, "improved_answer": "...", "strengths": ["..."], '
        '"suggestions": ["..."]}. "improved_answer" must be a genuine rewrite that adds '
        "detail, structure, or precision the original lacked -- never repeat the candidate's "
        "answer back verbatim or near-verbatim, even for a strong answer; if it is already "
        "excellent, extend it with one more layer of depth (an edge case, a tradeoff, a "
        "concrete example). Format improved_answer as markdown: use short paragraphs "
        "separated by blank lines (\\n\\n), bullet points (- ) for lists, and fenced code "
        "blocks (```python ... ```) for any code, never inline in a sentence. Keep it under "
        f"{IMPROVED_ANSWER_MAX_WORDS} words. \"strengths\" and \"suggestions\" are short "
        f"bullet-point strings, exactly {PROMPT_LIST_ITEM_COUNT} each -- brief, one line, no "
        "sub-explanations."
    )
    prompt = f"Question: {question}\n\nCandidate's answer: {answer}"

    log.info("Analyzing answer (%d chars) for: %s", len(answer), question[:80])
    data = _generate_json(
        prompt, system, num_predict=ANALYSIS_NUM_PREDICT, format_schema=ANALYSIS_JSON_SCHEMA
    )

    score = data.get("score", 0)
    try:
        score = max(MIN_SCORE, min(MAX_SCORE, int(round(float(score)))))
    except (TypeError, ValueError):
        score = MIN_SCORE

    strengths = data.get("strengths") or []
    suggestions = data.get("suggestions") or []
    improved_answer = str(data.get("improved_answer", "")).strip()

    # The model occasionally just echoes the candidate's answer back as the
    # "improvement" (most often when the answer already scored well). Showing
    # that verbatim as an "improved" version is misleading, so drop it rather
    # than display a non-improvement.
    if _normalize_for_comparison(improved_answer) == _normalize_for_comparison(answer):
        log.info("Model echoed the candidate's answer as the 'improvement' -- suppressing it")
        improved_answer = ""

    if improved_answer and not _looks_like_a_real_answer(improved_answer):
        log.warning(
            "Model returned a nonsensical 'improved_answer' (%r) -- suppressing it",
            improved_answer[:80],
        )
        improved_answer = ""

    strengths = [str(s).strip() for s in strengths if str(s).strip()][:MAX_STRENGTHS]
    suggestions = [str(s).strip() for s in suggestions if str(s).strip()][:MAX_SUGGESTIONS]

    # Every field below is shown as its own section in the UI. Rather than
    # let the frontend hide a section when the model's output was empty or
    # got suppressed above, always provide *something* -- a generic but
    # honest, score-appropriate fallback -- so the candidate never sees a
    # feedback screen with a missing section.
    if not strengths:
        log.warning("No usable strengths returned -- using generic fallback")
        strengths = list(_generic_strengths(score))
    if not suggestions:
        log.warning("No usable suggestions returned -- using generic fallback")
        suggestions = list(_generic_suggestions(score))
    if not improved_answer:
        log.warning("No usable improved_answer -- using generic fallback message")
        improved_answer = GENERIC_IMPROVED_ANSWER_FALLBACK

    log.info("Answer scored %d/10", score)
    return {
        "score": score,
        "strengths": strengths,
        "suggestions": suggestions,
        "improved_answer": improved_answer,
    }


def _looks_like_a_real_answer(text):
    """Sanity check for improved_answer: reject stray numbers, single tokens,
    or other garbage a small local model occasionally emits instead of a real
    rewrite (e.g. a lone '-2.1'). Requires a handful of alphabetic words --
    intentionally loose, since this only needs to catch obvious non-answers,
    not judge quality."""
    words = re.findall(r"[A-Za-z]+", text)
    return len(words) >= MIN_IMPROVED_ANSWER_WORDS


def _normalize_for_comparison(text):
    return re.sub(r"\s+", " ", text).strip().lower()


GENERIC_STRONG_STRENGTHS = [
    "Answer addressed the core of what the question asked.",
    "Explanation was clear and easy to follow.",
]
GENERIC_WEAK_STRENGTHS = [
    "Attempted to engage with the question directly.",
]
GENERIC_STRONG_SUGGESTIONS = [
    "Consider adding a concrete example or edge case for extra depth.",
]
GENERIC_WEAK_SUGGESTIONS = [
    "Add more detail and structure to fully answer the question.",
    "Include a concrete example to illustrate the key point.",
]
GENERIC_IMPROVED_ANSWER_FALLBACK = (
    "A stronger answer would add a concrete example, mention any relevant "
    "trade-offs, and briefly explain *why* the approach works -- not just what it is."
)


def _generic_strengths(score):
    """Score-appropriate fallback strengths, used only when the model's own
    output was empty -- kept honest by tone (weaker praise for weak scores)
    rather than pretending every answer has the same strengths."""
    return GENERIC_STRONG_STRENGTHS if score >= 6 else GENERIC_WEAK_STRENGTHS


def _generic_suggestions(score):
    """Score-appropriate fallback suggestions, mirroring _generic_strengths."""
    return GENERIC_STRONG_SUGGESTIONS if score >= 6 else GENERIC_WEAK_SUGGESTIONS


def generate_final_summary(role, personality, difficulty, average_score, answers):
    system = (
        f"{PERSONALITY_PROMPTS[personality]} You are writing the closing summary of an "
        f"interview for a {role} position. Respond with strict JSON only: "
        '{"summary": "..."}. 3-5 sentences: overall impression, one or two consistent '
        "strengths across the interview, and the single biggest area to improve."
    )
    transcript = "\n\n".join(
        f"Q{i+1} (score {a['score']}/10): {a['question_text']}\nAnswer: {a['answer_text']}"
        for i, a in enumerate(answers)
    )
    prompt = (
        f"Role: {role} | Difficulty: {difficulty} | Average score: {average_score:.1f}/10\n\n"
        f"{transcript}"
    )

    log.info("Generating final summary: role=%s avg_score=%.1f", role, average_score)
    data = _generate_json(prompt, system, num_predict=SUMMARY_NUM_PREDICT)
    summary = str(data.get("summary", "")).strip()
    return summary or "No summary available."


def count_filler_words(text):
    """Return the total filler-word count and which ones occurred, used as a
    communication-quality signal alongside the LLM's content-quality score."""
    lowered = text.lower()
    matched = []
    total = 0
    for phrase in FILLER_WORDS:
        count = len(re.findall(r"\b" + re.escape(phrase) + r"\b", lowered))
        if count:
            matched.append({"phrase": phrase, "count": count})
            total += count
    return total, matched
