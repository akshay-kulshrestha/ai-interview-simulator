# AI Interview Simulator

Master your interview skills with AI-powered practice. Choose a role, answer
real interview questions, and get instant feedback with scores, strengths,
and suggested improvements — practice with voice or text, anytime.

**100% local. No API key, no cloud AI, no internet connection required to
run it.** Every question comes from a local question bank, and every
answer is scored by a TF-IDF similarity model that runs entirely on your
own machine.

## Features

- **Local AI-Powered Analysis** — every answer is scored by comparing it,
  via TF-IDF + cosine similarity, against the expected concepts for that
  question — a real vector-space model, not a hardcoded keyword check,
  fit locally at startup from the question bank itself
- **Voice or Text Input** — type your answers or use speech-to-text to
  simulate a real conversation
- **Multiple Interviewer Styles** — practice with a friendly, professional,
  or strict interviewer, each adjusting feedback tone
- **Instant Feedback** — get a score, strengths, missed concepts, and
  pointers toward a stronger answer immediately after each response
- **Performance Tracking** — every session is saved to your history, so
  you can track progress and see where you improve over time
- **Detailed Final Report** — category breakdowns, best and worst
  answers, filler-word tracking, and a downloadable PDF after each
  interview

## How it works

1. **Select Role** — pick your job role, difficulty, and interviewer style
2. **Answer Questions** — type or speak your answers to real interview
   questions
3. **Get Feedback** — your answer is scored and analyzed instantly
4. **Review Report** — see strengths, gaps, and download your final report

## Job roles covered

Python Developer · Data Scientist · Web Developer · Software Engineer ·
AI Engineer · DevOps Engineer

84 questions across technical, behavioral, and problem-solving categories,
at 3 difficulty levels, all scored locally.

## Tech stack

| Layer | Technology |
|---|---|
| Scoring | TF-IDF + cosine similarity (`scikit-learn`), fit locally at startup |
| Backend | Flask, SQLite |
| PDF reports | `fpdf2` |
| Frontend | Jinja2 templates, vanilla CSS/JS |

## Project structure

```
ai-interview-simulator/
├── app.py                    # Flask app, routes
├── ai_analyzer.py               # Question bank + TF-IDF scoring model
├── interview.py                    # Session orchestration, question sequencing
├── database.py                        # SQLite persistence
├── report_pdf.py                         # PDF report generation
├── requirements.txt
├── env.example                             # Optional config (no API key needed)
├── .gitignore
├── README.md
├── static/
│   ├── css/style.css
│   └── js/interview.js
├── templates/
│   ├── base.html                               # Shared layout, nav, footer
│   ├── setup.html                                 # Role/difficulty selection
│   ├── interview.html                               # Live interview screen
│   ├── report.html                                    # Final report view
│   └── history.html                                     # Past session history
├── reports/                                               # Generated PDF reports (gitignored)
└── data/
    └── interviews.db                                        # SQLite database (created at runtime, gitignored)
```

## Setup

### 1. Install dependencies

```
pip install -r requirements.txt
```

### 2. Run the app

```
python app.py
```

Then open `http://127.0.0.1:5001` in your browser. That's it — no API key,
no environment variables required, no external service to configure.

## Notes

- `env.example` lists a few optional tuning knobs (database path, PDF
  output directory, port) — none are required to run the app.
- `.env`, the SQLite database, generated PDF reports, and `__pycache__/`
  are excluded from version control via `.gitignore`, since they're
  either machine-specific or regenerated automatically at runtime.
- Because scoring is entirely local, there are no rate limits, no usage
  costs, and no dependency on any third-party AI service ever being
  available or affordable.
