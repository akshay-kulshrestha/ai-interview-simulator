# AI Interview Simulator

Master your interview skills with AI-powered practice. Choose a role, answer
real interview questions, and get instant AI feedback with scores,
strengths, and suggested improvements — practice with voice or text,
anytime.

**Runs entirely on your machine.** No cloud AI, no accounts, no data
leaving your device — powered by a local Ollama model.

## Features

- **AI-Powered Analysis** — every answer is scored on keyword coverage,
  structure, clarity, and depth, with instant actionable feedback
- **Voice or Text Input** — type your answers or use speech-to-text to
  simulate a real conversation
- **Multiple Interviewer Styles** — practice with a friendly, professional,
  or strict interviewer, each adjusting feedback tone and scoring
- **Instant Feedback** — get scores, strengths, missed concepts, and a
  model-improved answer immediately after each response
- **Performance Tracking** — every session is saved to your history, so you
  can track progress and see where you improve over time
- **Detailed Final Report** — category breakdowns, best and worst answers,
  skill analysis, and downloadable reports after each interview

## How it works

1. **Select Role** — pick your job role, difficulty, and interviewer style
2. **Answer Questions** — type or speak your answers to real interview
   questions
3. **Get Feedback** — the AI scores and analyzes each answer instantly
4. **Review Report** — see strengths, gaps, and download your final report

## Job roles covered

Python Developer · Data Scientist · Web Developer · Software Engineer ·
AI Engineer · DevOps Engineer

100+ questions across 3 difficulty levels, with detailed 10-point scoring
per answer.

## Tech stack

| Layer | Technology |
|---|---|
| AI / LLM | [Ollama](https://ollama.com), running locally (`llama3.2`) |
| Backend | Flask, SQLite |
| PDF reports | `report_pdf.py` |
| Frontend | Jinja2 templates, custom CSS/JS |

## Project structure
ai_interview_simulator/
├── app.py # Flask app, routes
├── ai_analyzer.py # AI scoring/feedback logic (talks to Ollama)
├── database.py # SQLite persistence
├── interview.py # Interview flow / question logic
├── report_pdf.py # PDF report generation
├── requirements.txt
├── env.example # Template for required environment variables
├── static/
│ ├── css/style.css
│ └── js/interview.js
├── templates/
│ ├── base.html
│ ├── setup.html # Role/difficulty selection
│ ├── interview.html # Live interview screen
│ ├── report.html # Final report view
│ └── history.html # Past session history
├── reports/ # Generated PDF reports
└── data/ # SQLite database (created at runtime)


## Setup

### 1. Install dependencies

pip install -r requirements.txt


### 2. Set up environment variables

Copy `env.example` to `.env` and fill in any required values.

copy env.example .env


### 3. Install and start Ollama

This project runs its AI analysis locally via [Ollama](https://ollama.com).

winget install Ollama.Ollama


In one terminal window, start the Ollama server (leave this running):

ollama serve


In a separate terminal, pull the model this project uses:

ollama pull llama3.2


### 4. Run the app

python app.py


Then open `http://127.0.0.1:5001` in your browser.

## Notes

- The app expects Ollama to be running at `http://localhost:11434` — if
  you see a "warm-up failed" or "Ollama unreachable" error on startup,
  make sure `ollama serve` is running and `llama3.2` has been pulled.
- `.env`, the SQLite database, and `__pycache__/` are excluded from
  version control via `.gitignore` — they're either secrets or
  regenerated automatically at runtime.
