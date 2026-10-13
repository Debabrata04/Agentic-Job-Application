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

# Ordered (keywords, field_name) pairs - first keyword match against a field's
# label/name/placeholder wins. Order matters: more specific keywords first.
FIELD_KEYWORDS: list[tuple[list[str], str]] = [
    (["first name", "firstname", "given name"], "first_name"),
    (["last name", "lastname", "surname", "family name"], "last_name"),
    (["full name", "your name"], "full_name"),
    (["email"], "email"),
    (["phone", "mobile", "telephone"], "phone"),
    (["address line 1", "street address", "address1"], "address_line1"),
    (["address line 2", "address2"], "address_line2"),
    (["city"], "city"),
    (["state", "province"], "state"),
    (["zip", "postal"], "postal_code"),
    (["country"], "country"),
    (["linkedin"], "linkedin_url"),
    (["portfolio", "website"], "portfolio_url"),
    (["github"], "github_url"),
    (["work authorization", "authorized to work", "legally authorized"], "work_authorization"),
    (["sponsorship", "require sponsorship", "visa sponsorship"], "requires_sponsorship"),
    (["relocate", "relocation"], "willing_to_relocate"),
    (["years of experience", "experience (years)"], "years_of_experience"),
    (["gender"], "gender"),
    (["race", "ethnicity"], "race_ethnicity"),
    (["veteran"], "veteran_status"),
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


def _match_field_name(label_text: str) -> Optional[str]:
    label_lower = label_text.lower()
    for keywords, field_name in FIELD_KEYWORDS:
        if any(kw in label_lower for kw in keywords):
            return field_name
    return None


_FIELD_SELECTOR = "input:not([type=hidden]), textarea, select"
_APPLY_TEXT_RE = re.compile(r"apply", re.IGNORECASE)


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


def _select_combobox_option(page, scope, field, value) -> bool:
    """For react-select-style comboboxes (role=combobox, aria-haspopup=true):
    typing into them only filters a list rendered elsewhere in the DOM -
    nothing is actually "selected" (and the underlying form state stays
    empty) until you click the matching option, same as a real user would.
    Reading the input's typed text back (what the rest of this file does for
    plain inputs) is a false positive here - the filter text sits visibly in
    the box either way, selected or not."""
    try:
        field.click()
    except Exception:
        pass

    try:
        # This specific pattern (confirmed via live DOM inspection) opens the
        # menu via a dedicated button - aria-label="Toggle flyout" - sitting
        # next to the text input, not via clicking/focusing the input itself.
        # Clicking the input alone left the menu permanently closed (0 options
        # ever rendered, confirmed via logging) regardless of what was typed.
        container = field.locator(
            "xpath=ancestor::*[contains(@class,'control') or contains(@class,'select')][1]"
        )
        toggle = container.locator('button[aria-label*="toggle" i], button[aria-label*="flyout" i], button[aria-label*="menu" i]')
        toggle_count = toggle.count()
        print(f"[JobAgent] _select_combobox_option: toggle button count={toggle_count}", flush=True)
        if toggle_count > 0:
            toggle.first.click()
            page.wait_for_timeout(200)
    except Exception as e:
        print(f"[JobAgent] _select_combobox_option: toggle lookup/click failed: {e}", flush=True)

    try:
        field.press_sequentially(str(value), delay=15)
    except Exception:
        try:
            field.fill(str(value))
        except Exception:
            return False

    target = str(value).strip().lower()
    last_texts = []
    for attempt in range(15):  # ~3s at 200ms
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
            if text.lower() == target or target in text.lower():
                try:
                    opt.click()
                    return True
                except Exception as e:
                    print(f"[JobAgent] _select_combobox_option: matched option but click failed: {e}", flush=True)
                    return False
        last_texts = texts
        if attempt == 0 or attempt == 14:
            print(f"[JobAgent] _select_combobox_option attempt {attempt}: target={target!r}, found {count} [role=option] element(s): {texts}", flush=True)
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
    outcome = result_queue.get()  # blocks until the worker finishes and posts a result
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
      left open so you can review and submit it yourself. Exists because
      content-script-based autofill (the browser extension) can't reliably
      replicate what Playwright already does correctly here - no CSP
      restriction, no cross-origin iframe restriction, and more robust
      actionability-aware interaction than hand-rolled DOM scripting.
    """
    from playwright.sync_api import sync_playwright

    user = db.get(User, application.user_id)
    profile = db.query(CandidateProfile).filter(CandidateProfile.user_id == user.id).first()
    job_posting = db.get(JobPosting, application.job_posting_id)
    resume = db.get(Resume, application.resume_id) if application.resume_id else None

    field_values = _build_field_values(user, profile)
    filled_answers: dict[str, str] = {}
    missing_fields: list[str] = []
    optional_missing_fields: list[str] = []

    if not job_posting or not job_posting.url:
        return {
            "filled_answers": {},
            "missing_fields": ["Job posting has no apply URL"],
            "optional_missing_fields": [],
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
            for i in range(count):
                field = fields.nth(i)
                try:
                    label_text = (
                        field.get_attribute("aria-label")
                        or field.get_attribute("placeholder")
                        or field.get_attribute("name")
                        or ""
                    )
                    if not label_text:
                        field_id = field.get_attribute("id")
                        if field_id:
                            label_el = scope.locator(f'label[for="{field_id}"]')
                            if label_el.count():
                                label_text = label_el.first.inner_text()
                    if not label_text:
                        continue

                    is_required = field.get_attribute("required") is not None or field.get_attribute("aria-required") == "true"

                    # Unlike a browser extension (no JS, anywhere, can touch a
                    # file input's value - hard browser security restriction),
                    # Playwright's set_input_files() goes through the browser's
                    # automation protocol directly, bypassing that entirely -
                    # no OS file picker dialog ever opens.
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
                            combobox_ok = _select_combobox_option(page, scope, field, value)
                            _log(f"combobox field label={label_text!r} -> selected={combobox_ok}")
                            if combobox_ok:
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
                            # Simulate actual typing instead, then blur to trigger
                            # any on-blur "touched" styling.
                            try:
                                field.click()
                                field.clear()
                                field.press_sequentially(value, delay=15)
                                field.evaluate("el => el.blur()")
                            except Exception:
                                field.fill(value)  # fallback for fields that reject real keystrokes

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
                page.wait_for_timeout(3000)
                for field, value, tag in filled_field_refs:
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
                                _select_combobox_option(page, scope, field, value)
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
                            field.click()
                            field.clear()
                            field.press_sequentially(value, delay=15)
                            field.evaluate("el => el.blur()")
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
                page.screenshot(path=str(screenshot_path), full_page=True)
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
        "screenshot_path": str(screenshot_path),
        "apply_url": job_posting.url,
    }
