"""Best-effort form pre-filler for job application pages.

Runs headless (this fires unattended from the 6-hour discovery/match cycle, so
there's nobody watching a window). It never submits anything - it fills what it
can map confidently, screenshots the result, and leaves the rest for you to
finish by hand. Field matching is a simple keyword-over-label heuristic tuned
first for Workday-style forms (matching CandidateProfile's field set); expect
to extend FIELD_KEYWORDS as you see more ATS layouts in practice.
"""

import multiprocessing
import re
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.models import Application, CandidateProfile, JobPosting, Resume, User
from app.services.gemini_filler import ask_gemini_for_field_value

# Hard wall-clock budget for the field-scan + re-check portion of a fill, and
# shared with prefill_application below as the basis for how long it'll wait
# on the worker process before giving up on it as stuck. Confirmed necessary
# against a real posting (Airbnb's Greenhouse embed), whose form kept
# clearing its own combobox selections on every re-check pass.
MAX_PREFILL_SECONDS = 180

# Ordered (keywords, field_name) pairs - first keyword match against a field's
# label/name/placeholder wins. Order matters: more specific keywords first.
FIELD_KEYWORDS: list[tuple[list[str], str]] = [
    (["first name", "firstname", "given name"], "first_name"),
    (["last name", "lastname", "surname", "family name"], "last_name"),
    # Bare "name" matters as its own keyword, not just "full name"/"your name" -
    # confirmed against a real Lever-hosted posting (Palantir) whose field is
    # literally labeled just "Name", which neither of those phrases is a
    # substring of. Safe to list after first/last name above: those match
    # first and win for any label containing "first name"/"last name", so a
    # bare "name" fallback here only catches genuinely unqualified labels.
    (["full name", "your name", "name"], "full_name"),
    (["email"], "email"),
    (["phone", "mobile", "telephone"], "phone"),
    (["address line 1", "street address", "address1"], "address_line1"),
    (["address line 2", "address2"], "address_line2"),
    (["city"], "city"),
    (["state", "province"], "state"),
    (["zip", "postal"], "postal_code"),
    (["linkedin"], "linkedin_url"),
    (["portfolio", "website"], "portfolio_url"),
    (["github"], "github_url"),
    (["work authorization", "authorized to work", "legally authorized"], "work_authorization"),
    (["sponsorship", "require sponsorship", "visa sponsorship"], "requires_sponsorship"),
    (["relocate", "relocation"], "willing_to_relocate"),
    # "country" listed after the above, not before - it's a single generic
    # word prone to appearing inside unrelated longer questions (confirmed
    # directly: "Are you legally authorized to work in the country in which
    # you are applying?" and a visa-sponsorship question both contain
    # "country" as a substring, and since this used to be checked first,
    # both got mismatched to the country field - one attempted "India"
    # against a Yes/No dropdown and failed outright, the other never even
    # reached work_authorization's own keywords at all).
    (["country"], "country"),
    (["years of experience", "experience (years)"], "years_of_experience"),
    (["gender"], "gender"),
    (["race", "ethnicity"], "race_ethnicity"),
    (["veteran", "military status"], "veteran_status"),
    (["disability"], "disability_status"),
]


# The Profile tab's EEO dropdowns (frontend/index.html) store short internal
# codes (e.g. "not_protected_veteran"), not the human-readable text that
# actually appears as an option on a real application form (e.g. "I am not a
# protected veteran"). Searching dropdowns for the raw code never matches -
# confirmed via logging (0 options ever matched "not_protected_veteran",
# while "male"/"India" happened to work only because those particular codes
# are coincidentally also the display text). Translate to the EEO-standard
# wording these codes were modeled on before using them to fill anything.
_EEO_CODE_TO_TEXT = {
    "male": "Male",
    "female": "Female",
    "non_binary": "Non-binary",
    "hispanic_or_latino": "Hispanic or Latino",
    "white": "White",
    "black_or_african_american": "Black or African American",
    "native_hawaiian_or_pacific_islander": "Native Hawaiian or Other Pacific Islander",
    "asian": "Asian",
    "american_indian_or_alaska_native": "American Indian or Alaska Native",
    "two_or_more_races": "Two or More Races",
    "not_protected_veteran": "I am not a protected veteran",
    "protected_veteran": "I identify as one or more of the classifications of a protected veteran",
    "decline_classification": "I am a protected veteran, but I choose not to self-identify",
    "yes_disability": "Yes, I have a disability",
    "no_disability": "No, I do not have a disability",
}


def _build_field_values(user: User, profile: Optional[CandidateProfile]) -> dict[str, str]:
    values: dict[str, str] = {}
    full_name = user.full_name or ""
    parts = full_name.split(" ", 1)
    if parts and parts[0]:
        values["first_name"] = parts[0]
    if len(parts) > 1:
        values["last_name"] = parts[1]
    values["full_name"] = full_name
    if user.email:
        values["email"] = user.email
    if user.phone:
        values["phone"] = user.phone
    if user.years_of_experience is not None:
        values["years_of_experience"] = str(user.years_of_experience)

    if profile:
        for field in (
            "address_line1", "address_line2", "city", "state", "postal_code", "country",
            "linkedin_url", "portfolio_url", "github_url", "work_authorization",
            "gender", "race_ethnicity", "veteran_status", "disability_status",
        ):
            val = getattr(profile, field, None)
            if val:
                values[field] = _EEO_CODE_TO_TEXT.get(val, val)
        if profile.requires_sponsorship is not None:
            values["requires_sponsorship"] = "Yes" if profile.requires_sponsorship else "No"
        if profile.willing_to_relocate is not None:
            values["willing_to_relocate"] = "Yes" if profile.willing_to_relocate else "No"

    return {k: v for k, v in values.items() if v}


def _build_candidate_context(field_values: dict[str, str], resume: Optional[Resume]) -> str:
    """Plain-text summary handed to Gemini as the ONLY information it's
    allowed to answer from (see gemini_filler.py) - reuses field_values
    (already-resolved profile/user data) rather than re-reading the User/
    CandidateProfile rows again, plus the active resume's parsed text for
    anything profile fields don't cover (current employer, past job titles,
    skills, education - the kind of thing a keyword-less "Current firm
    you're part of?" field needs)."""
    lines = [f"{key.replace('_', ' ')}: {value}" for key, value in field_values.items()]
    context = "\n".join(lines)
    if resume and resume.parsed_text:
        context += "\n\nResume text:\n" + resume.parsed_text[:6000]
    return context


def _match_field_name(label_text: str) -> Optional[str]:
    label_lower = label_text.lower()
    for keywords, field_name in FIELD_KEYWORDS:
        for kw in keywords:
            # Word-boundary match, not plain substring - a short, generic
            # keyword like "city" is also a substring of unrelated words
            # (confirmed directly: "ethnicity" contains "city", so "What is
            # your race or ethnicity?" was incorrectly matched to the city
            # field and filled with the candidate's city instead of being
            # left for the race/ethnicity dropdown). Multi-word keywords
            # (e.g. "full name") still match as a phrase via the same regex.
            if re.search(r"(?<![a-z0-9])" + re.escape(kw) + r"(?![a-z0-9])", label_lower):
                return field_name
    return None


_FIELD_SELECTOR = "input:not([type=hidden]), textarea, select"
_APPLY_TEXT_RE = re.compile(r"apply", re.IGNORECASE)

# Catches custom-styled autocomplete suggestion lists that DON'T use standard
# ARIA combobox/listbox roles (so the react-select handling elsewhere in this
# file never sees them) - confirmed directly against a real Lever posting:
# typing a value into its "location" field shows a dropdown with class
# "dropdown-location" and the typed text gets silently cleared on blur
# unless an actual suggestion is clicked. Scoped to class-name patterns
# rather than one exact string since different ATS platforms style these
# differently, but "dropdown"/"suggest"/"autocomplete"/"option" cover the
# common naming conventions.
_SUGGESTION_ITEM_SELECTOR = (
    '[class*="dropdown-location" i], [class*="dropdown-result" i], '
    '[class*="suggestion" i], [class*="autocomplete-item" i], [role="option"]'
)


def _fill_text_like_field(page, field, value: str) -> None:
    """Shared fill logic for plain text inputs/textareas - types the value,
    then checks for a visible suggestion/autocomplete item that appeared as
    a result (see _SUGGESTION_ITEM_SELECTOR above) and clicks it if present,
    before falling back to a normal blur. Used by both the keyword-matched
    path and the Gemini fallback, since either can hit this same class of
    field."""
    try:
        field.click()
        field.clear()
        field.press_sequentially(value, delay=15)
    except Exception:
        field.fill(value)  # fallback for fields that reject real keystrokes
        return

    page.wait_for_timeout(600)  # let an async suggestion list render
    try:
        suggestions = page.locator(_SUGGESTION_ITEM_SELECTOR)
        if suggestions.count() > 0 and suggestions.first.is_visible():
            suggestions.first.click(timeout=3000)
            return
    except Exception:
        pass

    try:
        field.evaluate("el => el.blur()")
    except Exception:
        pass

    # Some masked/controlled inputs (confirmed on a phone field, which ended
    # up with trailing digits duplicated, e.g. "+919818545003545003") can
    # race their own reformat-as-you-type logic against press_sequentially's
    # per-character keystrokes, corrupting the typed value. Verify what
    # actually landed and, if it doesn't match what was typed, overwrite it
    # once with .fill() - which sets the raw value atomically in one go
    # rather than keystroke-by-keystroke, sidestepping the same race.
    try:
        current = field.input_value()
        if current and current.strip() and current.strip() != value.strip():
            field.fill(value)
    except Exception:
        pass


def _count_fields(frame) -> int:
    try:
        return frame.locator(_FIELD_SELECTOR).count()
    except Exception:
        return 0


def _best_field_scope(page):
    """Many ATS boards (Greenhouse embeds in particular) render the actual form
    inside an <iframe>. page.locator() only searches the main document, so we
    check every frame and use whichever one actually has form fields."""
    best_frame, best_count = page.main_frame, 0
    for frame in page.frames:
        count = _count_fields(frame)
        if count > best_count:
            best_frame, best_count = frame, count
    return best_frame, best_count


def _open_combobox_menu(page, field) -> None:
    """Shared menu-opening step used by both _select_combobox_option and
    _list_combobox_options - clicking the input alone doesn't open every
    variant's menu (confirmed via logging: some only respond to a dedicated
    "Toggle flyout"-style button sitting next to the input)."""
    try:
        field.scroll_into_view_if_needed(timeout=3000)
    except Exception:
        pass

    opened = False
    try:
        field.click(timeout=3000)
        opened = True
    except Exception as e:
        # A plain click can get intercepted by a previous combobox's menu
        # still animating closed (confirmed: this field opened fine in
        # isolation but failed in the real run immediately after an earlier
        # combobox's option-click, same page) - the exception was being
        # silently swallowed here, so the menu never got a second chance to
        # open at all. Dismiss any stale menu and force through once.
        print(f"[JobAgent] _open_combobox_menu: plain click failed ({e}), retrying with Escape+force", flush=True)
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        try:
            field.click(timeout=3000, force=True)
            opened = True
        except Exception as e2:
            print(f"[JobAgent] _open_combobox_menu: forced click also failed: {e2}", flush=True)

    try:
        # React's state update after the click handler fires is async - an
        # immediate aria-expanded read can still see the pre-click "false"
        # value even though the click itself genuinely worked, which made
        # the raw-mouse-click fallback below fire as an unwanted SECOND
        # click that just toggles an already-opening menu straight back
        # closed (confirmed as the likely cause of a field that opens fine
        # in isolation but consistently finds 0 options in the real run).
        page.wait_for_timeout(150)
        expanded = field.get_attribute("aria-expanded")
        if expanded != "true":
            # A second CLICK here is risky: react-select toggles the menu on
            # each click, so if the first click's open genuinely did land
            # (just not yet reflected in this aria-expanded read) a second
            # click would re-close it, and clicking the wrong overlapping
            # element entirely is also possible (force=True can "succeed"
            # with no exception while hitting something else at the same
            # coordinates). ArrowDown is a strictly additive, idempotent
            # open: react-select's standard keyboard handler opens a closed
            # menu on ArrowDown and otherwise just moves the active option -
            # it never closes an already-open one, so it's safe to fire
            # unconditionally here even if the first click already worked.
            print(f"[JobAgent] _open_combobox_menu: aria-expanded={expanded!r} after click (opened={opened}), trying focus+ArrowDown", flush=True)
            try:
                field.focus()
                page.keyboard.press("ArrowDown")
                page.wait_for_timeout(150)
            except Exception as e4:
                print(f"[JobAgent] _open_combobox_menu: focus+ArrowDown failed: {e4}", flush=True)
    except Exception as e3:
        print(f"[JobAgent] _open_combobox_menu: aria-expanded check / raw click failed: {e3}", flush=True)
    try:
        container = field.locator(
            "xpath=ancestor::*[contains(@class,'control') or contains(@class,'select')][1]"
        )
        toggle = container.locator('button[aria-label*="toggle" i], button[aria-label*="flyout" i], button[aria-label*="menu" i]')
        if toggle.count() > 0:
            toggle.first.click(timeout=3000)
            page.wait_for_timeout(200)
    except Exception as e:
        print(f"[JobAgent] _open_combobox_menu: toggle lookup/click failed: {e}", flush=True)

    # aria-expanded can flip to "true" (and the click/force-click report no
    # exception) before the option list has actually finished rendering/
    # animating into a Playwright-visible state - confirmed directly: a
    # field with no error logged at all up to this point still read back 0
    # [role=option] elements on the very first scan. A fixed settle wait
    # here (not just the retry further down, which re-opens an already-open
    # menu and does nothing new) gives slower renders a chance to finish
    # before the first scan ever runs.
    page.wait_for_timeout(250)


def _list_combobox_options(page, scope, field) -> list[str]:
    """Opens a react-select-style combobox's menu and reads back every
    visible option's text, so a question that the keyword matcher (or a
    simple yes/no guess) can't resolve can instead be handed to Gemini with
    the field's REAL choices - same pattern already used for native <select>
    and radio groups, extended to comboboxes which don't expose their
    options without first opening the menu."""
    # Opening the menu is flaky under real page load (confirmed directly:
    # the exact same field returned 7 options on one run and 0 on another,
    # no code change in between) - a couple of retries costs little against
    # the alternative of silently giving up on an otherwise-fillable field.
    for attempt in range(3):
        _open_combobox_menu(page, field)
        try:
            options = scope.locator('[role="option"]:visible')
            count = options.count()
            texts = []
            for oi in range(count):
                try:
                    text = options.nth(oi).inner_text().strip()
                except Exception:
                    continue
                if text:
                    texts.append(text)
            if texts:
                return texts
        except Exception as e:
            print(f"[JobAgent] _list_combobox_options: failed to read options (attempt {attempt}): {e}", flush=True)
        page.wait_for_timeout(300)
    return []


def _select_combobox_option(page, scope, field, value, deadline_check=None) -> bool:
    """For react-select-style comboboxes (role=combobox, aria-haspopup=true):
    typing into them only filters a list rendered elsewhere in the DOM -
    nothing is actually "selected" (and the underlying form state stays
    empty) until you click the matching option, same as a real user would.
    Reading the input's typed text back (what the rest of this file does for
    plain inputs) is a false positive here - the filter text sits visibly in
    the box either way, selected or not.

    deadline_check (optional): a zero-arg callable returning True once the
    caller's overall time budget is up - checked on every polling attempt so
    one stubborn combobox can't burn the whole budget by itself (up to ~45s
    otherwise: 15 attempts x ~3s each when every click times out)."""
    _open_combobox_menu(page, field)

    def _normalize_option_text(s: str) -> str:
        # Different companies word the same EEO/demographic option
        # differently (confirmed directly: one posting's disability-status
        # option read "No, I don't have a disability" while the candidate's
        # stored value was "No, I do not have a disability") - expanding
        # common contractions on both sides before comparing closes that
        # specific gap without needing full fuzzy matching.
        s = s.lower().strip().replace("’", "'")
        for contraction, expanded in (
            ("don't", "do not"), ("doesn't", "does not"), ("won't", "will not"),
            ("can't", "cannot"), ("isn't", "is not"), ("aren't", "are not"),
            ("i'm", "i am"),
        ):
            s = s.replace(contraction, expanded)
        return s

    target = _normalize_option_text(str(value))

    def _scan_and_click_matching_option():
        # :visible matters - other comboboxes on the page (e.g. the phone
        # field's country-code selector) can leave their own [role=option]
        # list mounted-but-hidden in the DOM, and an unscoped search can match
        # into the wrong one (observed: briefly matched intl-tel-input's
        # country list while filling the city field).
        options = scope.locator('[role="option"]:visible')
        count = options.count()
        texts = []
        for oi in range(count):
            opt = options.nth(oi)
            try:
                text = opt.inner_text().strip()
            except Exception:
                continue
            texts.append(text)
            if _normalize_option_text(text) == target or target in _normalize_option_text(text):
                try:
                    opt.click(timeout=3000)
                    return True, texts
                except Exception:
                    pass
                try:
                    opt.click(timeout=3000, force=True)
                    return True, texts
                except Exception as e:
                    print(f"[JobAgent] _select_combobox_option: matched option but click failed even with force: {e}", flush=True)
                    return False, texts
        return None, texts

    # Try matching against whatever the menu already shows from the click/
    # toggle above, BEFORE typing anything - typing runs the value through
    # react-select's own substring filter, which can legitimately filter
    # every real option out (confirmed directly: typing the full mismatched-
    # wording value left 0 options rendered at all, so the comparison logic
    # never even got a chance to run its own, more forgiving match).
    matched, texts = _scan_and_click_matching_option()
    if matched is True:
        return True
    if matched is False:
        return False
    for retry in range(3):
        if texts:
            break
        # Menu found 0 options - on a fast-moving React form (e.g. right
        # after an earlier field's own selection triggered a re-render), the
        # click can land before the menu has actually mounted. Confirmed
        # directly this is genuinely flaky (same field: 7 options on one
        # run, 0 on the next, no code change between them) - several
        # retries costs little against giving up on an otherwise-fillable
        # field.
        page.wait_for_timeout(300)
        _open_combobox_menu(page, field)
        matched, texts = _scan_and_click_matching_option()
        if matched is True:
            return True
        if matched is False:
            return False

    try:
        field.press_sequentially(str(value), delay=15)
    except Exception:
        try:
            field.fill(str(value))
        except Exception:
            return False

    last_texts = []
    for attempt in range(15):  # ~3s at 200ms
        if deadline_check and deadline_check():
            print(f"[JobAgent] _select_combobox_option: deadline exceeded mid-attempt (attempt {attempt}), giving up early", flush=True)
            return False
        matched, texts = _scan_and_click_matching_option()
        if matched is True:
            return True
        if matched is False:
            return False
        last_texts = texts
        if attempt == 0 or attempt == 14:
            print(f"[JobAgent] _select_combobox_option attempt {attempt}: target={target!r}, found {len(texts)} [role=option] element(s): {texts}", flush=True)
        page.wait_for_timeout(200)
    print(f"[JobAgent] _select_combobox_option gave up. target={target!r}, last seen options: {last_texts}", flush=True)
    return False


def _click_apply_button(page) -> bool:
    """job_posting.url is often a listing/description page with an "Apply"
    button/link leading to the real form - search every frame for it."""
    for frame in page.frames:
        try:
            apply_control = frame.get_by_role("link", name=_APPLY_TEXT_RE).or_(
                frame.get_by_role("button", name=_APPLY_TEXT_RE)
            )
            if apply_control.count() > 0:
                apply_control.first.click(timeout=5000)
                return True
        except Exception:
            continue
    return False


def prefill_application(db: Session, application: Application) -> dict:
    """Runs the actual Playwright fill in a separate OS process, not just a
    thread, via multiprocessing.

    Two things forced this: (1) Playwright's sync API spawns the browser as a
    subprocess, which on Windows needs ProactorEventLoop support - the default
    policy outside the main thread doesn't have it (NotImplementedError).
    (2) Globally changing the event loop policy to fix that (tried first)
    silently switched uvicorn's own server loop onto Proactor too, since the
    policy is process-wide - that's what hung the live server the moment a
    background task ran concurrently with real requests. A genuinely separate
    process has its own default policy from a clean interpreter start (which
    already works - this is exactly why standalone test scripts always worked
    fine), with zero interaction with uvicorn's process at all.

    Only application.id crosses the process boundary (plain picklable value);
    the worker opens its own fresh DB session rather than sharing db/application,
    which can't safely cross a process boundary anyway.
    """
    result_queue: multiprocessing.Queue = multiprocessing.Queue()
    process = multiprocessing.Process(
        target=_prefill_worker_process, args=(str(application.id), result_queue)
    )
    process.start()
    # MAX_PREFILL_SECONDS bounds the field-scan/re-check loop INSIDE the
    # worker, but anything after that point (full_page screenshot,
    # browser.close()) isn't covered by that in-worker deadline - confirmed
    # directly: a real run hit the in-worker deadline correctly (logs showed
    # it stopping the scan at ~192s) and then still never returned, because
    # result_queue.get() here had no timeout at all and blocked forever on a
    # worker stuck after that point. This generous outer bound (the in-worker
    # budget plus room for screenshot/cleanup) is the actual backstop -
    # terminate the process rather than hang the request indefinitely.
    try:
        outcome = result_queue.get(timeout=MAX_PREFILL_SECONDS + 60)
    except Exception:
        process.terminate()
        process.join(timeout=10)
        if process.is_alive():
            process.kill()
        raise RuntimeError(
            f"Prefill worker did not finish within {MAX_PREFILL_SECONDS + 60}s "
            "(stuck after the field-scan deadline, e.g. during screenshot capture "
            "or browser shutdown) - terminated."
        )
    process.join()
    if "error" in outcome:
        raise RuntimeError(outcome["error"])
    return outcome["value"]


def open_application_live(application_id: str) -> None:
    """Launches a REAL, visible Chromium window on your own desktop (this
    backend runs locally, so "visible" means a window actually appears on your
    screen) and fills it with the exact same logic prefill_application uses -
    but doesn't close it. You review and submit it yourself.

    Fire-and-forget: starts the subprocess and returns immediately without
    waiting for it, since the window may stay open for as long as you want.
    """
    process = multiprocessing.Process(target=_open_live_worker_process, args=(application_id,))
    process.start()


def _open_live_worker_process(application_id: str) -> None:
    import traceback

    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        application = db.get(Application, application_id)
        if not application:
            return
        _prefill_application_impl(db, application, headless=False, close_browser=False)
    except Exception:
        traceback.print_exc()
    finally:
        db.close()


def _prefill_worker_process(application_id: str, result_queue: "multiprocessing.Queue") -> None:
    """Runs in the child process (see prefill_application). Opens its own DB
    session since the parent's can't cross the process boundary."""
    import traceback

    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        application = db.get(Application, application_id)
        value = _prefill_application_impl(db, application)
        result_queue.put({"value": value})
    except Exception:
        result_queue.put({"error": traceback.format_exc()})
    finally:
        db.close()


def _prefill_application_impl(
    db: Session, application: Application, headless: bool = True, close_browser: bool = True
) -> dict:
    """Core fill logic, shared by both modes:
    - headless=True, close_browser=True: the unattended 6-hour-cycle path
      (prefill_application) - no one's watching, so it closes itself and
      leaves a screenshot + field data behind to review later.
    - headless=False, close_browser=False: the "open and fill live" path
      (open_application_live) - a real, visible window on your own desktop,
      left open so you can review and submit it yourself. Playwright drives
      this directly rather than injecting a content script into the page -
      no CSP restriction, no cross-origin iframe restriction, and more
      robust actionability-aware interaction than hand-rolled DOM scripting.
    """
    from playwright.sync_api import sync_playwright

    user = db.get(User, application.user_id)
    profile = db.query(CandidateProfile).filter(CandidateProfile.user_id == user.id).first()
    job_posting = db.get(JobPosting, application.job_posting_id)
    resume = db.get(Resume, application.resume_id) if application.resume_id else None

    field_values = _build_field_values(user, profile)
    candidate_context = _build_candidate_context(field_values, resume)
    filled_answers: dict[str, str] = {}
    missing_fields: list[str] = []
    optional_missing_fields: list[str] = []
    gemini_filled_fields: list[str] = []  # subset of filled_answers' keys the keyword matcher missed and Gemini caught

    if not job_posting or not job_posting.url:
        return {
            "filled_answers": {},
            "missing_fields": ["Job posting has no apply URL"],
            "optional_missing_fields": [],
            "gemini_filled_fields": [],
            "screenshot_path": None,
            "apply_url": None,
        }

    screenshot_dir = Path(settings.screenshot_storage_dir) / str(user.id)
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    screenshot_path = screenshot_dir / f"{application.id}.png"

    import time as _time
    _t0 = _time.time()

    def _log(msg):
        print(f"[JobAgent +{_time.time() - _t0:5.1f}s] {msg}", flush=True)

    # Past this deadline (module-level MAX_PREFILL_SECONDS), the field loop
    # and re-check passes stop early and whatever got filled so far is
    # returned - a partial, reviewable result beats a prefill that runs for
    # 30+ minutes and never finishes (confirmed against a real posting,
    # Airbnb's Greenhouse embed, whose form kept clearing its own combobox
    # selections on every re-check pass).
    _deadline = _t0 + MAX_PREFILL_SECONDS

    def _deadline_exceeded():
        return _time.time() > _deadline

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page()
        try:
            _log(f"navigating to {job_posting.url}")
            page.goto(job_posting.url, timeout=30000, wait_until="domcontentloaded")
            page.wait_for_timeout(1500)  # let client-rendered ATS forms (Workday, Greenhouse) settle

            scope, field_count = _best_field_scope(page)
            _log(f"initial field_count={field_count}")
            if field_count == 0 and _click_apply_button(page):
                # The form (often inside an iframe) can take a moment to appear
                # after the click, including the iframe itself loading - poll a
                # few times rather than a single fixed wait.
                for _ in range(5):
                    page.wait_for_timeout(1000)
                    scope, field_count = _best_field_scope(page)
                    if field_count > 0:
                        break

            # Do NOT click the "Attach" button - set_input_files() reported
            # success with no error when we did, but nothing was visually
            # attached. That strongly indicates the button opens the browser's
            # native OS file picker dialog directly (not a reveal-a-hidden-
            # input action), which Playwright/DOM automation cannot interact
            # with at all - and our set_input_files() call was then hitting
            # some other, disconnected input. The file input is almost
            # certainly already present in the DOM (just visually hidden via
            # CSS, with "Attach" wired to trigger the OS dialog on it) - target
            # it directly below instead, without ever clicking anything.
            if resume and resume.file_path:
                _log("resume: will target a file input directly in the scan below (no Attach click)")
            else:
                _log(f"resume: skipped (resume={resume}, file_path={getattr(resume, 'file_path', None)})")

            fields = scope.locator(_FIELD_SELECTOR)
            count = fields.count()
            _log(f"scanning {count} field(s)")
            filled_field_refs = []  # (field, value, tag) - for the re-check pass below
            resume_attached = False
            handled_radio_groups: set[str] = set()  # radio `name` groups already resolved this scan
            handled_checkbox_groups: set[str] = set()  # checkbox labels already skipped this scan (avoid burning quota on each option)

            def _try_gemini_fallback(field, label_text: str, is_required: bool) -> bool:
                """Second-pass fallback for whatever the keyword matcher
                (FIELD_KEYWORDS) couldn't map - only reached once that path
                has already failed, see the loop below. Only handles plain
                text-like inputs/textareas and native <select> - NOT
                react-select comboboxes, which don't expose their option
                list without first opening the menu (same complexity that
                makes _select_combobox_option's own retry logic nontrivial;
                extending Gemini to that case is a reasonable future step,
                not done here). Returns False (never raises) on anything
                that doesn't end in a confirmed, read-back-verified fill -
                same as a keyword-matcher miss, the caller falls through to
                missing_fields/optional_missing_fields."""
                try:
                    tag = field.evaluate("el => el.tagName.toLowerCase()")
                    is_combobox = (
                        field.get_attribute("role") == "combobox"
                        and field.get_attribute("aria-haspopup") == "true"
                    )
                    if is_combobox:
                        # Same closed-choice pattern as native <select> and
                        # radio groups below: open the menu, read back the
                        # REAL option labels, and let Gemini pick among them
                        # rather than guessing free text (which a react-select
                        # input would just filter against and never "select").
                        options = _list_combobox_options(page, scope, field)
                        if not options:
                            return False
                        value = ask_gemini_for_field_value(label_text, candidate_context, options)
                        if not value:
                            return False
                        ok = _select_combobox_option(page, scope, field, value, deadline_check=_deadline_exceeded)
                        if ok:
                            filled_answers[label_text.strip()] = value
                            gemini_filled_fields.append(label_text.strip())
                            return True
                        return False

                    if field.get_attribute("type") == "checkbox":
                        # A multi-select checkbox group (e.g. a 30-option
                        # "Language Skills" list) shares one label across
                        # every individual checkbox thanks to the container-
                        # based label lookup above - without a dedup here,
                        # each checkbox independently calls Gemini for the
                        # SAME question, burning through the daily free-tier
                        # quota before reaching any other field (confirmed
                        # directly: ~30 wasted calls for one question).
                        # Multi-select isn't supported by the single-value
                        # ask_gemini_for_field_value() flow anyway (that's a
                        # separate feature to add later) - skip outright
                        # rather than let it drain quota other fields need.
                        if label_text in handled_checkbox_groups:
                            return False
                        handled_checkbox_groups.add(label_text)
                        return False

                    options = None
                    if tag == "select":
                        options = field.evaluate("el => Array.from(el.options).map(o => o.text)")

                    value = ask_gemini_for_field_value(label_text, candidate_context, options)
                    if not value:
                        return False
                    _log(f"gemini fallback: label={label_text!r} -> {value!r}")

                    if tag == "select":
                        field.select_option(label=value)
                    else:
                        _fill_text_like_field(page, field, value)

                    actual_value = field.input_value() if tag != "select" else field.evaluate(
                        "el => el.options[el.selectedIndex] ? el.options[el.selectedIndex].text : ''"
                    )
                    if actual_value and actual_value.strip():
                        filled_answers[label_text.strip()] = actual_value.strip()
                        filled_field_refs.append((field, value, tag))
                        gemini_filled_fields.append(label_text.strip())
                        return True
                    return False
                except Exception as e:
                    _log(f"gemini fallback: error for label={label_text!r}: {e}")
                    return False

            for i in range(count):
                if _deadline_exceeded():
                    _log(f"deadline exceeded mid-scan at field {i}/{count} - stopping early")
                    break
                field = fields.nth(i)
                try:
                    label_text = (
                        field.get_attribute("aria-label")
                        or field.get_attribute("placeholder")
                        or ""
                    )
                    if not label_text:
                        field_id = field.get_attribute("id")
                        if field_id:
                            label_el = scope.locator(f'label[for="{field_id}"]')
                            if label_el.count():
                                label_text = label_el.first.inner_text()
                    if not label_text:
                        # Some ATS platforms (confirmed on Lever) render the real
                        # question text as a sibling "label" element inside a
                        # shared question container, with no aria-label,
                        # placeholder, or <label for> association on the field
                        # itself - every custom "cards[...]" question on a real
                        # Palantir/Lever posting fell through all the way to the
                        # opaque internal `name` attribute (e.g.
                        # "cards[1c719ca9-...][field0]") with nothing for the
                        # keyword matcher or Gemini to work with. Walk up to the
                        # nearest question-like container and pull the first
                        # label-like descendant that doesn't itself contain the
                        # field (so a <select>'s own wrapper isn't mistaken for
                        # the label), stripping any "required" marker span.
                        try:
                            label_text = field.evaluate(
                                """el => {
                                    // Prefer a container explicitly marked as a
                                    // "question" (or a <fieldset>) over a bare <li> -
                                    // radio options are themselves often each
                                    // wrapped in their own inner <li>, which
                                    // .closest('li') would grab instead of the
                                    // outer question container that actually holds
                                    // the real label (confirmed on a real Lever
                                    // posting's Yes/No radio markup).
                                    const container = el.closest('[class*="question" i], fieldset') || el.closest('li');
                                    if (!container) return '';
                                    const candidates = container.querySelectorAll('[class*="label" i]');
                                    for (const c of candidates) {
                                        if (c.contains(el)) continue;
                                        const clone = c.cloneNode(true);
                                        clone.querySelectorAll('[class*="required" i]').forEach(r => r.remove());
                                        const text = clone.textContent.trim();
                                        if (text) return text;
                                    }
                                    return '';
                                }"""
                            ) or ""
                        except Exception:
                            label_text = ""
                    if not label_text:
                        label_text = field.get_attribute("name") or ""
                    if not label_text:
                        continue

                    is_required = field.get_attribute("required") is not None or field.get_attribute("aria-required") == "true"

                    # Radio groups get no handling anywhere else in this loop -
                    # each <input type=radio> is just one more "field" to the
                    # generic text-fill path below, which fails silently on a
                    # radio (no text value to type/clear). Handle the whole
                    # group once (dedup by `name`), asking Gemini to pick among
                    # the group's real option labels (e.g. "Yes"/"No") rather
                    # than the keyword matcher, since these are closed-choice
                    # questions, not free text.
                    if field.get_attribute("type") == "radio":
                        radio_name = field.get_attribute("name") or ""
                        if not radio_name or radio_name in handled_radio_groups:
                            continue
                        handled_radio_groups.add(radio_name)
                        try:
                            group = scope.locator(f'input[type="radio"][name="{radio_name}"]')
                            group_count = group.count()
                            option_labels = []
                            for gi in range(group_count):
                                gfield = group.nth(gi)
                                opt_label = gfield.evaluate(
                                    """el => {
                                        const wrap = el.closest('label');
                                        if (wrap) return wrap.textContent.trim();
                                        const next = el.nextElementSibling;
                                        if (next) return next.textContent.trim();
                                        return el.value || '';
                                    }"""
                                ) or ""
                                option_labels.append(opt_label.strip())

                            picked = None
                            if any(option_labels):
                                picked = ask_gemini_for_field_value(label_text, candidate_context, option_labels)
                            if picked:
                                for gi in range(group_count):
                                    if option_labels[gi].strip().lower() == picked.strip().lower():
                                        group.nth(gi).click(timeout=3000)
                                        filled_answers[label_text.strip()] = picked
                                        gemini_filled_fields.append(label_text.strip())
                                        break
                                else:
                                    picked = None
                            if not picked:
                                if is_required:
                                    missing_fields.append(label_text.strip())
                                else:
                                    optional_missing_fields.append(label_text.strip())
                        except Exception as e:
                            _log(f"radio group fill error for {label_text!r}: {e}")
                            if is_required:
                                missing_fields.append(label_text.strip())
                            else:
                                optional_missing_fields.append(label_text.strip())
                        continue

                    # No JS, anywhere, can touch a file input's value - that's a
                    # hard browser security restriction. Playwright's
                    # set_input_files() goes through the browser's automation
                    # protocol directly, bypassing that entirely - no OS file
                    # picker dialog ever opens.
                    if field.get_attribute("type") == "file":
                        # The revealed file input's only "label" is usually just
                        # the Attach button's own text ("Attach") - confirmed via
                        # logging, so a resume/cv text match never succeeds here.
                        # We already specifically clicked the FIRST Attach button
                        # (resume section, before Cover Letter) earlier, so treat
                        # the first file input encountered as the resume slot
                        # instead of requiring a label match that can't work.
                        is_resume_slot = resume and resume.file_path and not resume_attached
                        _log(f"found file input, label={label_text!r}, treating as resume slot={bool(is_resume_slot)}")
                        if is_resume_slot:
                            try:
                                field.set_input_files(resume.file_path)
                                filled_answers[label_text.strip()] = resume.original_filename
                                resume_attached = True
                                _log(f"resume: set_input_files succeeded for {resume.file_path}")
                            except Exception as e:
                                _log(f"resume: set_input_files FAILED: {e}")
                                if is_required:
                                    missing_fields.append(label_text.strip())
                                else:
                                    optional_missing_fields.append(label_text.strip())
                        elif is_required:
                            missing_fields.append(label_text.strip())
                        else:
                            optional_missing_fields.append(label_text.strip())
                        continue

                    field_name = _match_field_name(label_text)

                    if field_name and field_name in field_values:
                        value = field_values[field_name]
                        tag = field.evaluate("el => el.tagName.toLowerCase()")
                        is_combobox = (
                            field.get_attribute("role") == "combobox"
                            and field.get_attribute("aria-haspopup") == "true"
                        )

                        if is_combobox:
                            _log(f"combobox field label={label_text!r}, target value={value!r} - attempting option click")
                            # react-select-style widget (Workday/Greenhouse etc.) -
                            # typing alone never "selects" anything; must click the
                            # resulting option. Skip the generic read-back check
                            # below entirely - a successful option click is already
                            # a much stronger confirmation than reading the filter
                            # text back (which sits there whether or not something
                            # was actually selected - false positive otherwise).
                            combobox_ok = _select_combobox_option(page, scope, field, value, deadline_check=_deadline_exceeded)
                            _log(f"combobox field label={label_text!r} -> selected={combobox_ok}")
                            if not combobox_ok:
                                # The hardcoded candidate value can fail to
                                # match ANY real option even though the field
                                # is genuinely the right one - confirmed
                                # directly: stored "I am not a protected
                                # veteran" matched none of the live options
                                # ("I identify as a non-protected veteran",
                                # etc.), only an exact-wording mismatch, not a
                                # missing/wrong field. Re-open, read back the
                                # real options, and let Gemini translate the
                                # candidate's actual answer onto this site's
                                # specific wording instead of giving up.
                                options = _list_combobox_options(page, scope, field)
                                if options:
                                    remapped = ask_gemini_for_field_value(label_text, candidate_context, options)
                                    if remapped:
                                        combobox_ok = _select_combobox_option(page, scope, field, remapped, deadline_check=_deadline_exceeded)
                                        if combobox_ok:
                                            value = remapped
                                            gemini_filled_fields.append(label_text.strip())
                            if combobox_ok:
                                # The option-click's menu-close animation can
                                # still be running when the NEXT combobox's
                                # click lands, intercepting it (confirmed
                                # directly: "military status" opened fine in
                                # isolation but failed right after "race or
                                # ethnicity" was filled in the same run).
                                page.wait_for_timeout(300)
                                filled_answers[label_text.strip()] = value
                                filled_field_refs.append((field, value, "combobox"))
                            elif is_required:
                                missing_fields.append(label_text.strip())
                            else:
                                optional_missing_fields.append(label_text.strip())
                            continue

                        if tag == "select":
                            try:
                                field.select_option(label=value)
                            except Exception:
                                pass
                        else:
                            # .fill() sets the value + fires input/change events,
                            # but some floating-label/styled components only pick
                            # up a value visually once they see real keystrokes
                            # (observed: value present in the DOM and read back
                            # fine, but rendered invisible in the screenshot).
                            # _fill_text_like_field simulates real typing and also
                            # handles custom-styled autocomplete suggestion lists
                            # (confirmed necessary against a real Lever posting -
                            # see its own docstring/comment for details).
                            _fill_text_like_field(page, field, value)

                        # Framework-controlled forms (React/Vue-driven ATS embeds)
                        # can silently reset a field after .fill() - e.g. an input
                        # mask rejecting the format, or a sibling field's
                        # validation re-rendering the form. Read the value back
                        # rather than trusting the fill call succeeded, since the
                        # email tells the user to copy these values verbatim.
                        actual_value = field.input_value() if tag != "select" else field.evaluate(
                            "el => el.options[el.selectedIndex] ? el.options[el.selectedIndex].text : ''"
                        )
                        if actual_value and actual_value.strip():
                            filled_answers[label_text.strip()] = actual_value.strip()
                            filled_field_refs.append((field, value, tag))
                        elif is_required:
                            missing_fields.append(label_text.strip())
                        else:
                            optional_missing_fields.append(label_text.strip())
                    elif not _deadline_exceeded() and _try_gemini_fallback(field, label_text, is_required):
                        pass
                    elif is_required:
                        missing_fields.append(label_text.strip())
                    else:
                        optional_missing_fields.append(label_text.strip())
                except Exception:
                    continue

            # Some ATS forms (Greenhouse in particular) asynchronously load a
            # saved "draft" of the application some unpredictable number of
            # seconds after the page renders - that fetch resolving after our
            # fill can silently wipe fields back to empty. A single fixed-delay
            # re-check isn't reliable (observed: one run it lands after the
            # clear and fixes it, another run the clear happens later and the
            # single check misses it) - so poll repeatedly over a longer window
            # instead of checking just once.
            _log(f"starting re-check passes, tracking {len(filled_field_refs)} field(s)")
            for pass_num in range(6):  # ~18s total coverage at 3s apart
                if _deadline_exceeded():
                    _log(f"deadline exceeded before re-check pass {pass_num} - stopping early")
                    break
                page.wait_for_timeout(3000)
                for field, value, tag in filled_field_refs:
                    if _deadline_exceeded():
                        break
                    try:
                        if tag == "combobox":
                            # Can't just read .input_value() to check this one -
                            # the filter text can still be sitting there whether
                            # or not a real option was ever selected. If the
                            # page's draft-reload cleared it, re-run the full
                            # click-type-click-option flow again.
                            current = field.input_value()
                            if not current or not current.strip():
                                _log(f"re-check pass {pass_num}: combobox value={value!r} was cleared, reselecting")
                                _select_combobox_option(page, scope, field, value, deadline_check=_deadline_exceeded)
                            continue
                        current = (
                            field.input_value()
                            if tag != "select"
                            else field.evaluate("el => el.options[el.selectedIndex] ? el.options[el.selectedIndex].text : ''")
                        )
                        if current and current.strip():
                            continue  # still filled, nothing to do
                        _log(f"re-check pass {pass_num}: field value={value!r} was cleared, refilling")
                        if tag == "select":
                            field.select_option(label=value)
                        else:
                            _fill_text_like_field(page, field, value)
                    except Exception as e:
                        _log(f"re-check pass {pass_num}: error re-filling: {e}")
                        continue
            _log("re-check passes complete")

            # The last .fill()/.select_option() call auto-scrolls its field into
            # view, so without this the full_page screenshot can start mid-form
            # (observed: it cut off the entire name/email/resume section above
            # the fold). Scroll both the page and the fillable scope (it may be
            # an iframe with its own scroll position) back to the top first.
            page.evaluate("window.scrollTo(0, 0)")
            try:
                scope.evaluate("window.scrollTo(0, 0)")
            except Exception:
                pass
            page.wait_for_timeout(300)
            if close_browser:
                page.screenshot(path=str(screenshot_path), full_page=True, timeout=15000)
        finally:
            if close_browser:
                browser.close()
            else:
                # Leave the window open for the user to review/submit themselves.
                # This call blocks (the caller already runs this in its own
                # fire-and-forget subprocess), returning once they close the
                # window on their own - so the subprocess exits cleanly instead
                # of leaking indefinitely.
                try:
                    while browser.is_connected():
                        page.wait_for_timeout(2000)
                except Exception:
                    pass

    return {
        "filled_answers": filled_answers,
        "missing_fields": sorted(set(missing_fields)),
        "optional_missing_fields": sorted(set(optional_missing_fields) - set(missing_fields)),
        "gemini_filled_fields": sorted(set(gemini_filled_fields)),
        "screenshot_path": str(screenshot_path),
        "apply_url": job_posting.url,
    }
