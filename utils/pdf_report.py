"""
pdf_report.py
Generate a PDF interview report with reportlab.

Public API:
  build_report_pdf(meta, results) -> bytes

  meta: dict with keys
        candidate_name, category, experience_level, interview_date,
        average_score, total_questions
  results: list of dicts, each with
        {"question": {"question": str, ...},
         "answer": str,
         "evaluation": {score, technical_accuracy, missing_concepts,
                        suggested_improvement, ideal_answer, _error?}}

Returns the PDF as bytes so Streamlit can stream it via st.download_button.
"""

import io

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable,
)

STRONG_THRESHOLD = 7
WEAK_THRESHOLD = 5

BRAND = colors.HexColor("#E8573F")
INK = colors.HexColor("#222222")
MUTED = colors.HexColor("#666666")


def _styles():
    base = getSampleStyleSheet()
    styles = {
        "title": ParagraphStyle(
            "title", parent=base["Title"], fontSize=20, textColor=INK,
            spaceAfter=4,
        ),
        "subtitle": ParagraphStyle(
            "subtitle", parent=base["Normal"], fontSize=10, textColor=MUTED,
            spaceAfter=12,
        ),
        "h2": ParagraphStyle(
            "h2", parent=base["Heading2"], fontSize=13, textColor=BRAND,
            spaceBefore=14, spaceAfter=6,
        ),
        "body": ParagraphStyle(
            "body", parent=base["Normal"], fontSize=10, leading=14,
            textColor=INK, alignment=TA_LEFT,
        ),
        "label": ParagraphStyle(
            "label", parent=base["Normal"], fontSize=10, leading=14,
            textColor=MUTED,
        ),
        "qhead": ParagraphStyle(
            "qhead", parent=base["Normal"], fontSize=11, leading=15,
            textColor=INK, spaceBefore=8, spaceAfter=2,
        ),
        "small": ParagraphStyle(
            "small", parent=base["Normal"], fontSize=9.5, leading=13,
            textColor=INK,
        ),
    }
    return styles


def _esc(text):
    """Escape characters that confuse reportlab's mini-HTML paragraphs."""
    if text is None:
        return ""
    return (str(text).replace("&", "&amp;")
            .replace("<", "&lt;").replace(">", "&gt;"))


def build_report_pdf(meta, results):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=16 * mm, bottomMargin=16 * mm,
        title="AI Mock Interview Report",
    )
    s = _styles()
    story = []

    # ---- Header ---------------------------------------------------------
    story.append(Paragraph("AI Mock Interview — Report", s["title"]))
    story.append(Paragraph("Data Analyst mock interview summary", s["subtitle"]))
    story.append(HRFlowable(width="100%", thickness=1, color=BRAND))
    story.append(Spacer(1, 10))

    # ---- Candidate / meta block ----------------------------------------
    info = [
        ["Candidate", _esc(meta.get("candidate_name", "")),
         "Average score", f"{meta.get('average_score', 0):.1f} / 10"],
        ["Category", _esc(meta.get("category", "")),
         "Total questions", str(meta.get("total_questions", ""))],
        ["Experience level", _esc(meta.get("experience_level", "")),
         "Interview date", _esc(meta.get("interview_date", ""))],
    ]
    tbl = Table(info, colWidths=[32 * mm, 55 * mm, 32 * mm, 45 * mm])
    tbl.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("TEXTCOLOR", (0, 0), (0, -1), MUTED),
        ("TEXTCOLOR", (2, 0), (2, -1), MUTED),
        ("TEXTCOLOR", (1, 0), (1, -1), INK),
        ("TEXTCOLOR", (3, 0), (3, -1), INK),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(tbl)
    story.append(Spacer(1, 6))

    # ---- Strong / improvement areas ------------------------------------
    scored = [r for r in results if not r["evaluation"].get("_error")
              and r["evaluation"].get("score")]
    strong = [r["question"]["question"] for r in scored
              if r["evaluation"]["score"] >= STRONG_THRESHOLD]
    weak = [r["question"]["question"] for r in scored
            if r["evaluation"]["score"] <= WEAK_THRESHOLD]

    story.append(Paragraph("Strong areas", s["h2"]))
    if strong:
        for q in strong:
            story.append(Paragraph(f"• {_esc(q)}", s["body"]))
    else:
        story.append(Paragraph(
            f"No answers scored {STRONG_THRESHOLD}+ this time.", s["label"]))

    story.append(Paragraph("Improvement areas", s["h2"]))
    if weak:
        for q in weak:
            story.append(Paragraph(f"• {_esc(q)}", s["body"]))
    else:
        story.append(Paragraph("No weak areas — nice work.", s["label"]))

    # ---- Question-wise feedback ----------------------------------------
    story.append(Paragraph("Question-wise feedback", s["h2"]))
    for i, r in enumerate(results, start=1):
        ev = r["evaluation"]
        q = r["question"]["question"]
        story.append(Paragraph(f"<b>Q{i}.</b> {_esc(q)}", s["qhead"]))

        if ev.get("_error"):
            story.append(Paragraph(
                "<i>Not scored (evaluation error).</i>", s["small"]))
        else:
            story.append(Paragraph(
                f"<b>Score:</b> {ev.get('score', '')}/10", s["small"]))
            story.append(Paragraph(
                f"<b>Your answer:</b> {_esc(r.get('answer', ''))}", s["small"]))
            story.append(Paragraph(
                f"<b>Technical accuracy:</b> "
                f"{_esc(ev.get('technical_accuracy', ''))}", s["small"]))
            story.append(Paragraph(
                f"<b>Missing concepts:</b> "
                f"{_esc(ev.get('missing_concepts', ''))}", s["small"]))
            story.append(Paragraph(
                f"<b>Suggested improvement:</b> "
                f"{_esc(ev.get('suggested_improvement', ''))}", s["small"]))
            story.append(Paragraph(
                f"<b>Ideal answer:</b> {_esc(ev.get('ideal_answer', ''))}",
                s["small"]))
        story.append(Spacer(1, 4))
        story.append(HRFlowable(width="100%", thickness=0.4,
                                color=colors.HexColor("#DDDDDD")))

    doc.build(story)
    buf.seek(0)
    return buf.getvalue()
