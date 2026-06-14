"""
claude_evaluator.py
Evaluates a candidate's interview answer using the Anthropic Claude API.

Responsibilities:
  - Read ANTHROPIC_API_KEY from .env
  - Send the question + answer to Claude
  - Force a strict JSON response and parse it
  - Retry on transient failures (timeouts, overloaded, connection errors)
  - Handle timeouts, empty responses, and invalid/non-JSON responses

Public API:
  evaluate_answer(question_text, user_answer, category, experience_level)
      -> dict with keys:
         score (int 1-10), technical_accuracy, missing_concepts,
         suggested_improvement, ideal_answer, and a private "_error" flag
         (False on success, True when a safe fallback result is returned).
"""

import json
import os
import re
import time

# python-dotenv is optional (not always present on Streamlit Cloud).
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import anthropic


def _get_secret(key, default=""):
    """Read config from env vars first, then Streamlit secrets."""
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
# Configuration
# ----------------------------------------------------------------------------
MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 1024
REQUEST_TIMEOUT = 30        # seconds per API call
MAX_RETRIES = 3
RETRY_BACKOFF = 2.0         # seconds; doubled each retry

REQUIRED_KEYS = [
    "score",
    "technical_accuracy",
    "missing_concepts",
    "suggested_improvement",
    "ideal_answer",
]


# ----------------------------------------------------------------------------
# Client
# ----------------------------------------------------------------------------
def _get_client():
    """
    Build an Anthropic client. Raises RuntimeError with a clear message if the
    API key is missing so the UI can show a friendly error.
    """
    api_key = _get_secret("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Add it to .env locally or to your "
            "Streamlit Cloud app secrets."
        )
    return anthropic.Anthropic(api_key=api_key, timeout=REQUEST_TIMEOUT)


# ----------------------------------------------------------------------------
# Prompt
# ----------------------------------------------------------------------------
def _build_prompt(question_text, user_answer, category, experience_level):
    return f"""You are an experienced technical interviewer for Data Analyst roles.
Evaluate the candidate's answer fairly for their experience level.

Category: {category}
Experience level: {experience_level}
Question: {question_text}

Candidate's answer:
\"\"\"{user_answer}\"\"\"

Respond with ONLY a single valid JSON object, no markdown, no commentary,
using exactly these keys:
{{
  "score": <integer 1-10>,
  "technical_accuracy": "<concise assessment of correctness>",
  "missing_concepts": "<key concepts the candidate did not mention>",
  "suggested_improvement": "<specific, actionable advice>",
  "ideal_answer": "<a strong model answer for this level>"
}}"""


# ----------------------------------------------------------------------------
# Response parsing
# ----------------------------------------------------------------------------
def _extract_json(text):
    """
    Parse a JSON object from the model's text. Tolerates stray prose or code
    fences by grabbing the outermost { ... } block. Returns a dict or None.
    """
    if not text or not text.strip():
        return None

    # Strip code fences if present.
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
    cleaned = re.sub(r"```$", "", cleaned).strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Fall back to the first {...} span.
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return None


def _normalize(data):
    """
    Validate keys and coerce types. Returns a clean evaluation dict, or None
    if the payload is missing required keys.
    """
    if not isinstance(data, dict):
        return None
    if any(k not in data for k in REQUIRED_KEYS):
        return None

    # Coerce score into an int within 1-10.
    try:
        score = int(round(float(data["score"])))
    except (TypeError, ValueError):
        score = 0
    score = max(1, min(10, score)) if score else 0

    return {
        "score": score,
        "technical_accuracy": str(data.get("technical_accuracy", "")).strip(),
        "missing_concepts": str(data.get("missing_concepts", "")).strip(),
        "suggested_improvement": str(data.get("suggested_improvement", "")).strip(),
        "ideal_answer": str(data.get("ideal_answer", "")).strip(),
        "_error": False,
    }


def _fallback(message):
    """A safe evaluation result used when the API or parsing fails."""
    return {
        "score": 0,
        "technical_accuracy": "Evaluation unavailable.",
        "missing_concepts": "",
        "suggested_improvement": "",
        "ideal_answer": "",
        "_error": True,
        "_message": message,
    }


# ----------------------------------------------------------------------------
# Public entry point
# ----------------------------------------------------------------------------
def evaluate_answer(question_text, user_answer, category, experience_level):
    """
    Evaluate one answer. Always returns a dict (never raises) so the UI flow
    can continue. On failure the dict has "_error": True and a "_message".
    """
    if not user_answer or not user_answer.strip():
        return _fallback("No answer provided.")

    try:
        client = _get_client()
    except RuntimeError as e:
        return _fallback(str(e))

    prompt = _build_prompt(question_text, user_answer, category, experience_level)

    last_error = "Unknown error."
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                messages=[{"role": "user", "content": prompt}],
            )

            # Empty response handling.
            if not response.content:
                last_error = "Empty response from Claude."
                raise ValueError(last_error)

            text = "".join(
                block.text for block in response.content
                if getattr(block, "type", None) == "text"
            )

            data = _extract_json(text)
            result = _normalize(data)
            if result is None:
                # Invalid / non-JSON response. Retry may help.
                last_error = "Invalid or non-JSON response from Claude."
                raise ValueError(last_error)

            return result

        except (anthropic.APITimeoutError, anthropic.APIConnectionError) as e:
            last_error = f"Connection/timeout error: {e}"
        except anthropic.RateLimitError as e:
            last_error = f"Rate limited: {e}"
        except anthropic.APIStatusError as e:
            # 5xx are worth retrying; 4xx (e.g. auth) are not.
            last_error = f"API error {e.status_code}: {e}"
            if e.status_code and e.status_code < 500:
                return _fallback(last_error)
        except ValueError as e:
            last_error = str(e)
        except Exception as e:  # noqa: BLE001 - last-resort safety net
            last_error = f"Unexpected error: {e}"

        if attempt < MAX_RETRIES:
            time.sleep(RETRY_BACKOFF * attempt)

    return _fallback(f"Evaluation failed after {MAX_RETRIES} attempts. {last_error}")
