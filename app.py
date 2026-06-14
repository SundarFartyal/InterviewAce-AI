"""
AI Mock Interview - Data Analyst (MVP v1.1)

Two pages (sidebar):
  - Interview:  setup -> one question at a time -> results
  - History:    table of past interviews + CSV export

Questions come ONLY from data/questions.csv. The Claude API is used ONLY to
evaluate answers. Answers shorter than MIN_ANSWER_LENGTH are rejected before
any API call. Each evaluated answer is saved to answers.csv immediately.

Run with:  streamlit run app.py
"""

import io
import random
import re
import statistics
from datetime import datetime

import streamlit as st

from utils import data_handler
from utils.claude_evaluator import evaluate_answer
from utils.pdf_report import build_report_pdf

# ----------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------
CATEGORIES = ["SQL", "Python", "Statistics", "Power BI", "HR"]
EXPERIENCE_LEVELS = ["Fresher", "1-3 Years", "3-5 Years"]

STAGE_SETUP = "setup"
STAGE_INTERVIEW = "interview"
STAGE_RESULTS = "results"

STRONG_THRESHOLD = 7      # score >= 7 -> strong area
WEAK_THRESHOLD = 5        # score <= 5 -> improvement area
MIN_ANSWER_LENGTH = 20    # characters; shorter answers are rejected

QUESTION_COUNT_OPTIONS = [2, 3, 5, 10, 15]
DEFAULT_QUESTION_COUNT = 10

APP_TITLE = "InterviewAce AI"
APP_SUBTITLE = "AI Mock Interview Platform for Data Analysts"
APP_VERSION = "Version 1.0"

EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def is_valid_email(email):
    return bool(EMAIL_REGEX.match((email or "").strip()))


# ----------------------------------------------------------------------------
# Styling
# ----------------------------------------------------------------------------
CUSTOM_CSS = """
<style>
    .block-container { max-width: 880px; padding-top: 2.5rem; padding-bottom: 4rem; }
    h1 { font-weight: 700; letter-spacing: -0.5px; }
    h2, h3 { font-weight: 600; }
    .stButton>button { border-radius: 8px; padding: 0.5rem 1.25rem; font-weight: 600; }
    .question-card {
        background: rgba(127,127,127,0.07);
        border: 1px solid rgba(127,127,127,0.18);
        border-radius: 12px;
        padding: 1.25rem 1.5rem;
        font-size: 1.15rem;
        line-height: 1.5;
        margin: 0.5rem 0 1.25rem 0;
    }
    div[data-testid="stMetric"] {
        background: rgba(127,127,127,0.06);
        border-radius: 12px;
        padding: 0.75rem 1rem;
    }
</style>
"""


# ----------------------------------------------------------------------------
# Session state
# ----------------------------------------------------------------------------
def init_session_state():
    defaults = {
        "stage": STAGE_SETUP,
        "candidate_name": "",
        "email": "",
        "category": CATEGORIES[0],
        "experience_level": EXPERIENCE_LEVELS[0],
        "num_questions": DEFAULT_QUESTION_COUNT,
        "interview_id": None,
        "started_at": None,
        "questions": [],
        "current_index": 0,
        "results": [],
        "last_evaluation": None,
        "last_answer": "",
        "feedback_submitted": False,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def reset_interview():
    st.session_state.stage = STAGE_SETUP
    st.session_state.interview_id = None
    st.session_state.started_at = None
    st.session_state.questions = []
    st.session_state.current_index = 0
    st.session_state.results = []
    st.session_state.last_evaluation = None
    st.session_state.last_answer = ""
    st.session_state.feedback_submitted = False


# ----------------------------------------------------------------------------
# Setup screen
# ----------------------------------------------------------------------------
def render_setup():
    st.subheader("Set up your mock interview")
    st.caption("Pick a topic and level. Questions come from your local bank; "
               "Claude scores each answer.")

    name_col, email_col = st.columns(2)
    with name_col:
        st.session_state.candidate_name = st.text_input(
            "Your name", value=st.session_state.candidate_name,
            placeholder="e.g. Sundar",
        )
    with email_col:
        st.session_state.email = st.text_input(
            "Email address", value=st.session_state.email,
            placeholder="e.g. you@example.com",
        )
    col1, col2, col3 = st.columns(3)
    with col1:
        st.session_state.category = st.selectbox(
            "Topic", CATEGORIES, index=CATEGORIES.index(st.session_state.category)
        )
    with col2:
        st.session_state.experience_level = st.selectbox(
            "Experience level", EXPERIENCE_LEVELS,
            index=EXPERIENCE_LEVELS.index(st.session_state.experience_level),
        )
    with col3:
        st.session_state.num_questions = st.selectbox(
            "Number of questions", QUESTION_COUNT_OPTIONS,
            index=QUESTION_COUNT_OPTIONS.index(st.session_state.num_questions),
        )

    st.write("")
    if st.button("Start interview", type="primary"):
        name = st.session_state.candidate_name.strip()
        email = st.session_state.email.strip()
        if not name:
            st.warning("Please enter your name to continue.")
            return
        if not is_valid_email(email):
            st.warning("Please enter a valid email address (e.g. you@example.com).")
            return

        try:
            questions = data_handler.load_questions(
                st.session_state.category, st.session_state.experience_level
            )
        except FileNotFoundError as e:
            st.error(f"Could not load questions: {e}")
            return

        if not questions:
            st.warning("No questions found for that combination. Try another.")
            return

        # Randomly pick the requested number of unique questions (never repeat).
        requested = st.session_state.num_questions
        selected = random.sample(questions, min(requested, len(questions)))
        if len(selected) < requested:
            st.info(
                f"Only {len(selected)} questions are available for this "
                f"combination, so the interview will use all of them."
            )

        try:
            st.session_state.interview_id = data_handler.create_interview(
                name, email, st.session_state.category,
                st.session_state.experience_level
            )
        except Exception as e:  # noqa: BLE001
            st.error(f"Storage is not configured correctly: {e}")
            return
        st.session_state.feedback_submitted = False
        st.session_state.started_at = datetime.now()
        st.session_state.questions = selected
        st.session_state.current_index = 0
        st.session_state.results = []
        st.session_state.last_evaluation = None
        st.session_state.last_answer = ""
        st.session_state.stage = STAGE_INTERVIEW
        st.success(f"Loaded {len(questions)} questions. Good luck!")
        st.rerun()


# ----------------------------------------------------------------------------
# Interview screen
# ----------------------------------------------------------------------------
def _render_evaluation(evaluation):
    if evaluation.get("_error"):
        st.error(
            "Could not score this answer: "
            f"{evaluation.get('_message', 'unknown error')}. "
            "Your answer was saved — you can continue."
        )
        return

    st.success("Answer evaluated.")
    st.metric("Score", f"{evaluation['score']} / 10")
    st.markdown(f"**Technical accuracy**  \n{evaluation['technical_accuracy']}")
    st.markdown(f"**Missing concepts**  \n{evaluation['missing_concepts']}")
    st.markdown(f"**Suggested improvement**  \n{evaluation['suggested_improvement']}")
    with st.expander("Show ideal answer"):
        st.markdown(evaluation["ideal_answer"])


def render_interview():
    questions = st.session_state.questions
    idx = st.session_state.current_index
    total = len(questions)
    question = questions[idx]

    st.caption(
        f"{st.session_state.category} · {st.session_state.experience_level}"
    )
    st.progress((idx + (1 if st.session_state.last_evaluation else 0)) / total,
                text=f"Question {idx + 1} of {total}")
    st.markdown(f"<div class='question-card'>{question['question']}</div>",
                unsafe_allow_html=True)

    # Already evaluated -> show feedback + advance button.
    if st.session_state.last_evaluation is not None:
        st.text_area("Your answer", value=st.session_state.last_answer,
                     disabled=True, key=f"shown_answer_{idx}")
        _render_evaluation(st.session_state.last_evaluation)

        is_last = idx + 1 >= total
        label = "Finish interview ✓" if is_last else "Next question →"
        if st.button(label, type="primary"):
            st.session_state.last_evaluation = None
            st.session_state.last_answer = ""
            if is_last:
                _finalize_and_go_to_results()
            else:
                st.session_state.current_index += 1
            st.rerun()
        return

    # Collect + validate + evaluate.
    answer = st.text_area("Your answer", key=f"answer_input_{idx}", height=200,
                          placeholder="Type your answer here...")
    char_count = len(answer.strip())
    st.caption(f"{char_count} characters (minimum {MIN_ANSWER_LENGTH})")

    if st.button("Submit answer", type="primary"):
        if char_count < MIN_ANSWER_LENGTH:
            st.warning("Please provide a more detailed answer.")
            return

        with st.spinner("Evaluating your answer with Claude..."):
            evaluation = evaluate_answer(
                question["question"], answer,
                st.session_state.category, st.session_state.experience_level,
            )

        data_handler.save_answer(
            st.session_state.interview_id, question, answer, evaluation
        )
        st.session_state.results.append(
            {"question": question, "answer": answer, "evaluation": evaluation}
        )
        st.session_state.last_evaluation = evaluation
        st.session_state.last_answer = answer
        st.rerun()


def _finalize_and_go_to_results():
    scores = [
        r["evaluation"]["score"] for r in st.session_state.results
        if not r["evaluation"].get("_error") and r["evaluation"]["score"]
    ]
    avg = statistics.mean(scores) if scores else 0.0
    data_handler.finalize_interview(
        st.session_state.interview_id,
        total_questions=len(st.session_state.results),
        average_score=avg,
    )
    st.session_state.stage = STAGE_RESULTS


# ----------------------------------------------------------------------------
# Feedback form (shown on the results page)
# ----------------------------------------------------------------------------
def _render_feedback_form():
    st.markdown("#### How useful was this interview?")

    if st.session_state.feedback_submitted:
        st.success("Thanks for your feedback!")
        return

    with st.form("feedback_form"):
        rating = st.radio(
            "Rating", [1, 2, 3, 4, 5], index=4, horizontal=True,
            format_func=lambda n: "⭐" * n,
        )
        comments = st.text_area("Comments", placeholder="Optional comments...")
        submitted = st.form_submit_button("Submit feedback", type="primary")

    if submitted:
        try:
            data_handler.save_feedback(
                st.session_state.interview_id,
                st.session_state.candidate_name,
                st.session_state.email,
                rating,
                comments.strip(),
            )
            st.session_state.feedback_submitted = True
            st.rerun()
        except Exception as e:  # noqa: BLE001
            st.warning(f"Could not save feedback: {e}")


# ----------------------------------------------------------------------------
# Results screen
# ----------------------------------------------------------------------------
def render_results():
    st.subheader("Interview report")
    results = st.session_state.results

    if not results:
        st.info("No answers were recorded.")
        if st.button("Start a new interview"):
            reset_interview()
            st.rerun()
        return

    scored = [r for r in results if not r["evaluation"].get("_error")
              and r["evaluation"]["score"]]
    scores = [r["evaluation"]["score"] for r in scored]
    avg = statistics.mean(scores) if scores else 0.0
    date_str = (st.session_state.started_at or datetime.now()).strftime(
        "%d %b %Y, %H:%M"
    )

    c1, c2, c3 = st.columns(3)
    c1.metric("Average score", f"{avg:.1f} / 10")
    c2.metric("Total questions", len(results))
    c3.metric("Scored", len(scored))

    st.markdown(
        f"**Candidate:** {st.session_state.candidate_name}  \n"
        f"**Category:** {st.session_state.category}  \n"
        f"**Experience level:** {st.session_state.experience_level}  \n"
        f"**Interview date:** {date_str}"
    )

    st.divider()

    strong = [r["question"]["question"] for r in scored
              if r["evaluation"]["score"] >= STRONG_THRESHOLD]
    weak = [r["question"]["question"] for r in scored
            if r["evaluation"]["score"] <= WEAK_THRESHOLD]

    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown("#### ✅ Strong areas")
        if strong:
            for q in strong:
                st.markdown(f"- {q}")
        else:
            st.caption(f"No answers scored {STRONG_THRESHOLD}+ this time.")
    with col_b:
        st.markdown("#### ⚠️ Improvement areas")
        if weak:
            for q in weak:
                st.markdown(f"- {q}")
        else:
            st.caption("No weak areas — nice work.")

    st.divider()
    st.markdown("#### Question-by-question")
    for i, r in enumerate(results, start=1):
        ev = r["evaluation"]
        if ev.get("_error"):
            st.markdown(f"**Q{i}.** {r['question']['question']}  \n"
                        f"_Not scored (evaluation error)._")
        else:
            st.markdown(
                f"**Q{i}.** {r['question']['question']}  \n"
                f"Score: **{ev['score']}/10** — {ev['technical_accuracy']}"
            )

    st.divider()
    _render_feedback_form()

    st.divider()

    # PDF report download.
    meta = {
        "candidate_name": st.session_state.candidate_name,
        "category": st.session_state.category,
        "experience_level": st.session_state.experience_level,
        "interview_date": date_str,
        "average_score": avg,
        "total_questions": len(results),
    }
    try:
        pdf_bytes = build_report_pdf(meta, results)
        safe_name = (st.session_state.candidate_name or "candidate").replace(
            " ", "_")
        st.download_button(
            "📄 Download PDF Report",
            data=pdf_bytes,
            file_name=f"interview_report_{safe_name}.pdf",
            mime="application/pdf",
        )
    except Exception as e:  # noqa: BLE001 - never block the results page
        st.warning(f"Could not generate the PDF report: {e}")

    if st.button("Start a new interview", type="primary"):
        reset_interview()
        st.rerun()


# ----------------------------------------------------------------------------
# History page
# ----------------------------------------------------------------------------
def _history_csv_bytes(rows):
    """Build a downloadable CSV (in memory) from the history rows."""
    import csv
    buf = io.StringIO()
    fields = ["interview_id", "candidate_name", "email", "category",
              "experience_level", "total_questions", "average_score",
              "started_at", "completed_at"]
    writer = csv.DictWriter(buf, fieldnames=fields)
    writer.writeheader()
    for r in rows:
        writer.writerow({k: r.get(k, "") for k in fields})
    return buf.getvalue().encode("utf-8")


def render_history():
    st.subheader("Interview history")
    try:
        rows = data_handler.load_interviews(completed_only=True)
    except Exception as e:  # noqa: BLE001
        st.error(f"Could not load history. Storage is not configured: {e}")
        return

    if not rows:
        st.info("No completed interviews yet. Finish one to see it here.")
        return

    # ---- Dashboard metrics ---------------------------------------------
    total_interviews = len(rows)
    unique_users = len({
        (r.get("email", "").strip().lower() or r.get("candidate_name", "").strip().lower())
        for r in rows
    })
    scores = []
    for r in rows:
        try:
            scores.append(float(r.get("average_score", "")))
        except (TypeError, ValueError):
            continue
    avg_score = statistics.mean(scores) if scores else None
    avg_rating = data_handler.average_rating()

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total interviews", total_interviews)
    m2.metric("Unique users", unique_users)
    m3.metric("Average score", f"{avg_score:.1f}" if avg_score is not None else "—")
    m4.metric("Average rating",
              f"{avg_rating:.1f} ⭐" if avg_rating is not None else "—")

    st.divider()

    # ---- Search ---------------------------------------------------------
    query = st.text_input("Search by candidate name or email",
                          placeholder="Type a name or email...").strip().lower()
    if query:
        rows = [
            r for r in rows
            if query in r.get("candidate_name", "").lower()
            or query in r.get("email", "").lower()
        ]
        st.caption(f"{len(rows)} match(es).")

    table = [
        {
            "Date": (r.get("started_at", "") or "").replace("T", " ")[:16],
            "Candidate": r.get("candidate_name", ""),
            "Email": r.get("email", ""),
            "Category": r.get("category", ""),
            "Experience": r.get("experience_level", ""),
            "Avg score": r.get("average_score", ""),
            "Questions": r.get("total_questions", ""),
        }
        for r in rows
    ]
    st.dataframe(table, use_container_width=True, hide_index=True)

    st.download_button(
        "⬇ Export history as CSV",
        data=_history_csv_bytes(
            data_handler.load_interviews(completed_only=True)),
        file_name="interview_history.csv",
        mime="text/csv",
    )

    # ---- Reviews / feedback --------------------------------------------
    st.divider()
    st.markdown("#### Reviews")
    feedback = data_handler.load_feedback()
    if not feedback:
        st.caption("No reviews submitted yet.")
    else:
        feedback.sort(key=lambda r: str(r.get("created_at", "")), reverse=True)
        review_table = []
        for r in feedback:
            try:
                stars = "⭐" * int(float(r.get("rating", 0)))
            except (TypeError, ValueError):
                stars = ""
            review_table.append({
                "Date": (r.get("created_at", "") or "").replace("T", " ")[:16],
                "Candidate": r.get("candidate_name", ""),
                "Email": r.get("email", ""),
                "Rating": stars or r.get("rating", ""),
                "Comments": r.get("comments", "") or "—",
            })
        st.dataframe(review_table, use_container_width=True, hide_index=True)


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
def render_interview_page():
    stage = st.session_state.stage
    if stage == STAGE_SETUP:
        render_setup()
    elif stage == STAGE_INTERVIEW:
        render_interview()
    elif stage == STAGE_RESULTS:
        render_results()
    else:
        st.error(f"Unknown stage: {stage}")


def main():
    st.set_page_config(page_title=APP_TITLE, page_icon="🎯",
                       layout="centered")
    st.markdown(CUSTOM_CSS, unsafe_allow_html=True)
    init_session_state()

    st.title(f"🎯 {APP_TITLE}")
    st.caption(APP_SUBTITLE)

    page = st.sidebar.radio("Navigate", ["Interview", "History"])
    st.sidebar.divider()
    st.sidebar.caption(f"{APP_VERSION} · local & CSV-based")

    if page == "Interview":
        render_interview_page()
    else:
        render_history()


if __name__ == "__main__":
    main()
