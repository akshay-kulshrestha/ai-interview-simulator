"""Renders a completed interview session's report as a downloadable PDF."""
import os

from fpdf import FPDF

BASE_DIR = os.path.dirname(__file__)
REPORTS_DIR = os.environ.get("REPORTS_DIR", os.path.join(BASE_DIR, "reports"))

# The core PDF fonts (Helvetica/Times/Courier) only support Latin-1. Kept
# as defensive cleanup even though improved_answer/summary are now built
# from plain Python string templates (not LLM output) rather than a model
# that could emit arbitrary Unicode -- smart quotes, em dashes, and bullet
# characters can still show up in a candidate's own typed answer, and any
# one of them raises FPDFUnicodeEncodingException with the core font,
# crashing the PDF download entirely. Map the common cases to clean ASCII,
# then fall back to safely dropping anything still unsupported (e.g. an
# emoji someone typed) so a single unexpected character can never break
# the whole report.
UNICODE_REPLACEMENTS = {
    "\u2018": "'", "\u2019": "'",   # curly single quotes
    "\u201c": '"', "\u201d": '"',   # curly double quotes
    "\u2013": "-", "\u2014": "-",   # en dash, em dash
    "\u2026": "...",                 # ellipsis
    "\u2022": "-",                   # bullet
    "\u00a0": " ",                   # non-breaking space
}


def _pdf_safe(text):
    if text is None:
        return ""
    text = str(text)
    for bad, good in UNICODE_REPLACEMENTS.items():
        text = text.replace(bad, good)
    return text.encode("latin-1", errors="replace").decode("latin-1")


def _write_wrapped(pdf, text, size=11, style=""):
    pdf.set_font("Helvetica", style, size)
    pdf.multi_cell(0, 6, _pdf_safe(text), new_x="LMARGIN", new_y="NEXT")


class InterviewReportPDF(FPDF):
    def header(self):
        self.set_font("Helvetica", "B", 16)
        self.cell(0, 10, "AI Interview Report", ln=True)
        self.set_draw_color(200, 200, 200)
        self.line(10, 20, 200, 20)
        self.ln(6)


def generate_pdf(report, force_regenerate=False):
    os.makedirs(REPORTS_DIR, exist_ok=True)
    session = report["session"]
    answers = report["answers"]
    file_path = os.path.join(REPORTS_DIR, f"interview_{session['id']}.pdf")

    # A completed session's report content never changes (answers are final,
    # and interview.py caches the summary once generated), so re-rendering
    # on every download is wasted CPU/IO. Reuse the existing file unless the
    # caller explicitly asks for a fresh one.
    if not force_regenerate and os.path.exists(file_path):
        return file_path

    pdf = InterviewReportPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    _write_wrapped(pdf, f"Role: {session['role']}", size=13, style="B")
    _write_wrapped(pdf, f"Difficulty: {session['difficulty'].title()}   |   Interviewer style: {session['personality'].title()}")
    _write_wrapped(pdf, f"Date: {session['started_at'][:10]}")
    score = session["overall_score"] or 0
    _write_wrapped(pdf, f"Overall score: {score:.1f} / 10", size=13, style="B")
    _write_wrapped(pdf, f"Filler words used: {report['total_filler_words']}")
    pdf.ln(4)
    if session.get("summary"):
        _write_wrapped(pdf, "Summary", size=12, style="B")
        _write_wrapped(pdf, session["summary"])
        pdf.ln(4)
    for i, answer in enumerate(answers, start=1):
        pdf.set_draw_color(220, 220, 220)
        pdf.line(10, pdf.get_y(), 200, pdf.get_y())
        pdf.ln(4)
        _write_wrapped(pdf, f"Q{i} [{answer['category'].upper()}]  Score: {answer['score']}/10", size=12, style="B")
        _write_wrapped(pdf, answer["question_text"], style="I")
        pdf.ln(1)
        _write_wrapped(pdf, "Your answer:", style="B")
        _write_wrapped(pdf, answer["answer_text"])
        pdf.ln(1)
        if answer["strengths"]:
            _write_wrapped(pdf, "Strengths:", style="B")
            for s in answer["strengths"]:
                _write_wrapped(pdf, f"  + {s}")
        if answer["suggestions"]:
            _write_wrapped(pdf, "Suggestions:", style="B")
            for s in answer["suggestions"]:
                _write_wrapped(pdf, f"  - {s}")
        if answer["improved_answer"]:
            _write_wrapped(pdf, "Improved answer:", style="B")
            _write_wrapped(pdf, answer["improved_answer"])
        if answer["filler_word_count"]:
            _write_wrapped(pdf, f"Filler words: {answer['filler_word_count']}")
        pdf.ln(4)
    pdf.output(file_path)
    return file_path