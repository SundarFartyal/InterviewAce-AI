"""
data_handler.py
Storage layer for the AI Mock Interview app.

Questions are still read from the local data/questions.csv (the question bank).
Interviews, Answers, and Feedback are now stored in Google Sheets (three tabs:
"Interviews", "Answers", "Feedback") via gspread + a Google service account.

The public function names and signatures are unchanged from the previous
CSV version, so the rest of the app (app.py, pdf_report.py) is untouched:

    load_questions(category, experience_level)
    create_interview(candidate_name, email, category, experience_level)
    finalize_interview(interview_id, total_questions, average_score)
    load_interviews(completed_only=True)
    save_answer(interview_id, question, user_answer, evaluation)
    save_feedback(interview_id, candidate_name, email, rating, comments)
    load_feedback()
    average_rating()

Configuration (read from .env):
    GOOGLE_SHEET_KEY              -> the spreadsheet ID (preferred), OR
    GOOGLE_SHEET_NAME            -> the spreadsheet title (fallback)
    GOOGLE_SERVICE_ACCOUNT_FILE  -> path to the service account JSON
                                    (default: <project>/service_account.json)
"""

import csv
import os
from datetime import datetime

# python-dotenv is optional. Locally it loads a .env file; on Streamlit Cloud
# (where the package may not be installed) we silently skip it and rely on
# environment variables / st.secrets instead.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import gspread


# ----------------------------------------------------------------------------
# Config helper (env var first, then Streamlit secrets)
# ----------------------------------------------------------------------------
def get_secret(key, default=""):
    """
    Return a config value, checking os.environ first, then st.secrets.
    Works locally (.env / real env vars) and on Streamlit Cloud (secrets),
    and never fails if streamlit/secrets are unavailable.
    """
    value = os.getenv(key)
    if value is not None:
        return value
    try:
        import streamlit as st
        if key in st.secrets:
            return st.secrets[key]
    except Exception:
        pass
    return default


# ----------------------------------------------------------------------------
# Paths / config
# ----------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
QUESTIONS_CSV = os.path.join(DATA_DIR, "questions.csv")

SERVICE_ACCOUNT_FILE = get_secret(
    "GOOGLE_SERVICE_ACCOUNT_FILE",
    os.path.join(BASE_DIR, "service_account.json"),
)
SHEET_KEY = str(get_secret("GOOGLE_SHEET_KEY", "")).strip()
SHEET_NAME = str(get_secret("GOOGLE_SHEET_NAME", "InterviewAce AI")).strip()

# Worksheet (tab) names and their headers.
INTERVIEWS_TAB = "Interviews"
ANSWERS_TAB = "Answers"
FEEDBACK_TAB = "Feedback"

INTERVIEW_FIELDS = [
    "interview_id", "candidate_name", "email", "category",
    "experience_level", "total_questions", "average_score",
    "started_at", "completed_at",
]
ANSWER_FIELDS = [
    "answer_id", "interview_id", "question_id", "question", "user_answer",
    "score", "technical_accuracy", "missing_concepts",
    "suggested_improvement", "ideal_answer", "answered_at",
]
FEEDBACK_FIELDS = [
    "feedback_id", "interview_id", "candidate_name", "email",
    "rating", "comments", "created_at",
]


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


# ----------------------------------------------------------------------------
# Google Sheets connection (lazy + cached)
# ----------------------------------------------------------------------------
_client = None
_spreadsheet = None
_worksheets = {}


def _service_account_info():
    """
    On Streamlit Cloud the service account is stored as a secrets dict under
    [gcp_service_account]. Return that dict if present, else None.
    """
    try:
        import streamlit as st
        if "gcp_service_account" in st.secrets:
            return dict(st.secrets["gcp_service_account"])
    except Exception:
        pass
    return None


def _get_client():
    global _client
    if _client is None:
        info = _service_account_info()
        if info:
            # Streamlit Cloud path: build credentials from the secrets dict.
            _client = gspread.service_account_from_dict(info)
        elif os.path.exists(SERVICE_ACCOUNT_FILE):
            # Local path: use the JSON key file.
            _client = gspread.service_account(filename=SERVICE_ACCOUNT_FILE)
        else:
            raise RuntimeError(
                "No Google credentials found. Locally, place "
                "service_account.json in the project root (or set "
                "GOOGLE_SERVICE_ACCOUNT_FILE). On Streamlit Cloud, add a "
                "[gcp_service_account] section to your app secrets."
            )
    return _client


def _get_spreadsheet():
    global _spreadsheet
    if _spreadsheet is None:
        client = _get_client()
        if SHEET_KEY:
            _spreadsheet = client.open_by_key(SHEET_KEY)
        elif SHEET_NAME:
            _spreadsheet = client.open(SHEET_NAME)
        else:
            raise RuntimeError(
                "No spreadsheet configured. Set GOOGLE_SHEET_KEY (preferred) "
                "or GOOGLE_SHEET_NAME in .env."
            )
    return _spreadsheet


def _get_worksheet(title, header):
    """
    Return the worksheet by title, creating it (with a header row) if needed,
    and ensuring the header row is present. Cached per title.
    """
    if title in _worksheets:
        return _worksheets[title]

    ss = _get_spreadsheet()
    try:
        ws = ss.worksheet(title)
    except gspread.WorksheetNotFound:
        ws = ss.add_worksheet(title=title, rows=1000, cols=max(len(header), 12))
        ws.append_row(header, value_input_option="RAW")

    # Ensure a header row exists / is correct.
    first_row = ws.row_values(1)
    if not first_row:
        ws.update("A1", [header])

    _worksheets[title] = ws
    return ws


def _records(ws):
    """Thin wrapper around get_all_records (list of dicts keyed by header)."""
    return ws.get_all_records()


def _update_matching_row(ws, header, match_field, match_value, updates):
    """
    Find the first data row where match_field == match_value and update the
    given fields. Returns True if a row was updated.
    """
    match_col = header.index(match_field) + 1
    # gspread 6.x returns None when not found (no CellNotFound exception).
    cell = ws.find(str(match_value), in_column=match_col)
    if not cell:
        return False

    row = cell.row
    for field, value in updates.items():
        col = header.index(field) + 1
        ws.update_cell(row, col, value)
    return True


# ----------------------------------------------------------------------------
# Questions (still CSV)
# ----------------------------------------------------------------------------
def load_questions(category, experience_level):
    if not os.path.exists(QUESTIONS_CSV):
        raise FileNotFoundError(f"questions.csv not found at {QUESTIONS_CSV}")

    matches = []
    with open(QUESTIONS_CSV, "r", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if (row.get("category", "").strip() == category
                    and row.get("experience_level", "").strip()
                    == experience_level):
                matches.append({
                    "question_id": row.get("question_id", "").strip(),
                    "category": row.get("category", "").strip(),
                    "experience_level": row.get("experience_level", "").strip(),
                    "question": row.get("question", "").strip(),
                })
    return matches


# ----------------------------------------------------------------------------
# Interviews (Google Sheets)
# ----------------------------------------------------------------------------
def create_interview(candidate_name, email, category, experience_level):
    ws = _get_worksheet(INTERVIEWS_TAB, INTERVIEW_FIELDS)
    interview_id = "INT-" + datetime.now().strftime("%Y%m%d-%H%M%S")
    row = {
        "interview_id": interview_id,
        "candidate_name": candidate_name,
        "email": email,
        "category": category,
        "experience_level": experience_level,
        "total_questions": 0,
        "average_score": "",
        "started_at": now_iso(),
        "completed_at": "",
    }
    ws.append_row([row[k] for k in INTERVIEW_FIELDS],
                  value_input_option="USER_ENTERED")
    return interview_id


def finalize_interview(interview_id, total_questions, average_score):
    ws = _get_worksheet(INTERVIEWS_TAB, INTERVIEW_FIELDS)
    _update_matching_row(
        ws, INTERVIEW_FIELDS, "interview_id", interview_id,
        {
            "total_questions": total_questions,
            "average_score": round(average_score, 2) if average_score else "",
            "completed_at": now_iso(),
        },
    )


def load_interviews(completed_only=True):
    ws = _get_worksheet(INTERVIEWS_TAB, INTERVIEW_FIELDS)
    rows = [{k: r.get(k, "") for k in INTERVIEW_FIELDS} for r in _records(ws)]
    if completed_only:
        rows = [r for r in rows if str(r.get("completed_at", "")).strip()]
    rows.sort(key=lambda r: str(r.get("started_at", "")), reverse=True)
    return rows


# ----------------------------------------------------------------------------
# Answers (Google Sheets)
# ----------------------------------------------------------------------------
def save_answer(interview_id, question, user_answer, evaluation):
    ws = _get_worksheet(ANSWERS_TAB, ANSWER_FIELDS)
    answer_id = "ANS-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    row = {
        "answer_id": answer_id,
        "interview_id": interview_id,
        "question_id": question.get("question_id", ""),
        "question": question.get("question", ""),
        "user_answer": user_answer,
        "score": evaluation.get("score", ""),
        "technical_accuracy": evaluation.get("technical_accuracy", ""),
        "missing_concepts": evaluation.get("missing_concepts", ""),
        "suggested_improvement": evaluation.get("suggested_improvement", ""),
        "ideal_answer": evaluation.get("ideal_answer", ""),
        "answered_at": now_iso(),
    }
    ws.append_row([row[k] for k in ANSWER_FIELDS],
                  value_input_option="USER_ENTERED")
    return answer_id


# ----------------------------------------------------------------------------
# Feedback (Google Sheets)
# ----------------------------------------------------------------------------
def save_feedback(interview_id, candidate_name, email, rating, comments):
    ws = _get_worksheet(FEEDBACK_TAB, FEEDBACK_FIELDS)
    feedback_id = "FB-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    row = {
        "feedback_id": feedback_id,
        "interview_id": interview_id,
        "candidate_name": candidate_name,
        "email": email,
        "rating": rating,
        "comments": comments,
        "created_at": now_iso(),
    }
    ws.append_row([row[k] for k in FEEDBACK_FIELDS],
                  value_input_option="USER_ENTERED")
    return feedback_id


def load_feedback():
    ws = _get_worksheet(FEEDBACK_TAB, FEEDBACK_FIELDS)
    return [{k: r.get(k, "") for k in FEEDBACK_FIELDS} for r in _records(ws)]


def average_rating():
    ratings = []
    for row in load_feedback():
        try:
            ratings.append(float(row.get("rating", "")))
        except (TypeError, ValueError):
            continue
    if not ratings:
        return None
    return sum(ratings) / len(ratings)
