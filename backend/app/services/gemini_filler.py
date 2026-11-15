"""LLM fallback for form fields the keyword matcher (FIELD_KEYWORDS in
application_filler.py) can't map - a label worded too differently from the
known keyword list to match via substring search (e.g. "Where can we reach
you?" for phone, or "What firm are you currently with?" for current
employer), where embedding/fuzzy matching would also likely miss.

This is deliberately a SECOND pass, not a replacement: the fast, free,
deterministic keyword matcher still runs first and handles every field it
already handles today, completely unchanged. Gemini is only consulted for
whatever's left unmatched, and only answers from data the user has actually
provided (profile + resume text) - it's told explicitly to say NONE rather
than invent a plausible-sounding answer, since a wrong guess silently
submitted on a real job application is worse than leaving the field for
missing_fields (which already drives the "needs more info" email - so a
NONE here costs nothing new, it's the same fallback path that existed
before this feature).
"""

import json
import re

import httpx

from app.core.config import settings

GEMINI_MODEL = "gemini-3.5-flash"
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"

# Keep the HTTP call itself short - this runs inside the same per-field loop
# whose overall budget is MAX_PREFILL_SECONDS (application_filler.py), and a
# slow/hanging LLM call for one field shouldn't be able to eat a large chunk
# of that budget the way a flaky combobox click already can.
_REQUEST_TIMEOUT_SECONDS = 10

_NO_MATCH_SENTINEL = "NONE"


def _build_prompt(label_text: str, context: str, options: list[str] | None) -> str:
    base = (
        "You are filling out one field of a real job application form on behalf "
        "of a candidate, using ONLY the information explicitly given below. "
        f'The form field is labeled: "{label_text}"\n\n'
        f"Candidate information:\n{context}\n\n"
    )
    if options:
        options_block = "\n".join(f"- {opt}" for opt in options)
        return (
            base
            + "This field is a dropdown with exactly these options:\n"
            + options_block
            + "\n\nReply with ONLY the exact text of the single best-matching option, "
            f"copied verbatim from the list above. If none of the options are "
            f"supported by the candidate information given, reply with exactly: {_NO_MATCH_SENTINEL}"
        )
    return (
        base
        + "Reply with ONLY the value to type into this field - no explanation, "
        "no quotes, nothing else. If the candidate information given doesn't "
        f"actually answer this field, reply with exactly: {_NO_MATCH_SENTINEL}"
    )


def ask_gemini_for_field_value(label_text: str, context: str, options: list[str] | None = None) -> str | None:
    """Returns a value to fill, or None if Gemini has no basis to answer (or
    the call fails/isn't configured) - callers treat None exactly like a
    keyword-matcher miss, falling through to missing_fields as before."""
    if not settings.gemini_api_key or not label_text.strip() or not context.strip():
        return None

    prompt = _build_prompt(label_text, context, options)
    try:
        response = httpx.post(
            GEMINI_URL,
            params={"key": settings.gemini_api_key},
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = response.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except httpx.HTTPStatusError as e:
        # Logged distinctly from a genuine "no answer" - confirmed directly
        # that a quota-exhausted 429 (free tier: 20 requests/day) silently
        # produced the exact same None as a legitimate miss, making a quota
        # problem indistinguishable from "Gemini had nothing to say" without
        # digging through a raw HTTP call by hand. _log in application_filler
        # isn't reachable from here, so this goes to stdout directly - still
        # picked up by the same process log the rest of the fill run writes to.
        print(f"[JobAgent] gemini_filler: HTTP {e.response.status_code} for label={label_text!r}: {e.response.text[:300]}", flush=True)
        return None
    except Exception as e:
        print(f"[JobAgent] gemini_filler: request failed for label={label_text!r}: {e}", flush=True)
        return None

    if not text or text.upper() == _NO_MATCH_SENTINEL:
        return None

    if options:
        # Only trust an exact (case-insensitive) match against a REAL option -
        # a close-but-not-exact string would never match the real <option>
        # text when the caller goes to select it, so it's as good as no answer.
        for opt in options:
            if opt.strip().lower() == text.lower():
                return opt
        return None

    return text


def draft_referral_message(candidate_context: str, job_context: str, connection_name: str) -> str | None:
    """Drafts a LinkedIn referral-request message for the user to review and
    send themselves - this function only generates text, nothing is ever
    sent or typed into LinkedIn from here. Returns None if Gemini isn't
    configured or the call fails; the caller surfaces that as an error
    rather than falling back to a template, since a referral ask is a
    one-shot, visible-to-a-real-person message worth failing loudly on."""
    if not settings.gemini_api_key:
        return None

    prompt = (
        "Draft a short, warm, professional LinkedIn direct message from a job "
        f"candidate to {connection_name or 'a LinkedIn connection'}, asking for a "
        "referral for a specific open role at their company. Use ONLY the facts "
        "given below - do not invent schools, employers, achievements, or details "
        "not stated here.\n\n"
        f"Candidate background:\n{candidate_context}\n\n"
        f"Role being asked about:\n{job_context}\n\n"
        "The message must naturally cover: where the candidate studied, where "
        "they currently work and what they do there, one or two concrete "
        "achievements, why they're interested in this specific role, and what "
        "makes them a good fit. End with a polite, low-pressure ask for a "
        "referral, and include the job ID so the connection can look it up. "
        "Keep it to 120-180 words, first person, no subject line, no "
        "placeholders like [Name] - write it ready to send as-is. Reply with "
        "ONLY the message text."
    )
    try:
        response = httpx.post(
            GEMINI_URL,
            params={"key": settings.gemini_api_key},
            json={"contents": [{"parts": [{"text": prompt}]}]},
            # Longer than the per-field fill timeout above - this isn't
            # racing a per-field budget inside a live browser session, and a
            # free-form 120-180 word message takes noticeably longer to
            # generate than a one-line field value (confirmed directly: the
            # shared 10s field-fill timeout timed out here every time).
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except httpx.HTTPStatusError as e:
        print(f"[JobAgent] gemini_filler: HTTP {e.response.status_code} drafting referral message: {e.response.text[:300]}", flush=True)
        return None
    except Exception as e:
        print(f"[JobAgent] gemini_filler: referral message draft failed: {e}", flush=True)
        return None

    return text or None


_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def extract_jd_requirements(jd_text: str) -> dict | None:
    """First-pass screening signal for matching.py: asks Gemini to read a job
    description and extract (a) the minimum years of experience it asks for,
    and (b) every closely-related profession/job-title a candidate in that
    role could reasonably be called (e.g. a "Machine Learning Engineer" JD
    should list "AI Engineer", "Data Scientist", "Software Engineer (ML)",
    etc.) - this is what lets the screen treat those as interchangeable
    instead of requiring an exact title match. Computed once per job posting
    and cached on JobPosting (gemini_required_years/gemini_related_professions),
    never recomputed per resume/user/cycle - see matching.py.

    Returns {"required_years": float|None, "related_professions": list[str]}
    or None if Gemini isn't configured, the call fails, or the response can't
    be parsed - callers treat None as "no screening signal available" and let
    the candidate through rather than blocking on a missing signal."""
    if not settings.gemini_api_key or not jd_text.strip():
        return None

    prompt = (
        "Read this job description and answer two questions as JSON only, "
        "no other text:\n"
        '1. "required_years": the minimum years of professional experience '
        "it asks for, as a number (e.g. 3). If none is stated, use null.\n"
        '2. "related_professions": a list of 4-8 job titles/professions that '
        "are close enough to this role that someone with that title would be "
        "a reasonable fit for it (e.g. for a Machine Learning Engineer JD: "
        '["Machine Learning Engineer", "AI Engineer", "Data Scientist", '
        '"Applied Scientist", "Software Engineer - ML", "Research Engineer"]). '
        "Base this on the actual responsibilities/skills in the JD, not just "
        "its title.\n\n"
        f"Reply with ONLY a JSON object like "
        '{"required_years": 3, "related_professions": ["...", "..."]}'
        f".\n\nJob description:\n{jd_text[:4000]}"
    )
    try:
        response = httpx.post(
            GEMINI_URL,
            params={"key": settings.gemini_api_key},
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=20,
        )
        response.raise_for_status()
        data = response.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except httpx.HTTPStatusError as e:
        print(f"[JobAgent] gemini_filler: HTTP {e.response.status_code} extracting JD requirements: {e.response.text[:300]}", flush=True)
        return None
    except Exception as e:
        print(f"[JobAgent] gemini_filler: JD requirements extraction failed: {e}", flush=True)
        return None

    match = _JSON_BLOCK_RE.search(text)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None

    required_years = parsed.get("required_years")
    try:
        required_years = float(required_years) if required_years is not None else None
    except (TypeError, ValueError):
        required_years = None

    related_professions = parsed.get("related_professions")
    if not isinstance(related_professions, list):
        related_professions = []
    related_professions = [str(p).strip() for p in related_professions if str(p).strip()]

    return {"required_years": required_years, "related_professions": related_professions}
