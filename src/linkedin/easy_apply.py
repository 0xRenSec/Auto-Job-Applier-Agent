"""The Easy Apply state machine: open a job, fill the modal, submit (or dry-run).

LinkedIn changes its DOM often. All the selectors live here near the top so they
are easy to update when something breaks.
"""
from __future__ import annotations

import re
from pathlib import Path

from playwright.sync_api import Locator, Page, TimeoutError as PWTimeout

from .. import cover_letter
from .. import resume as resume_mod
from ..llm_answers import llm_answer
from ..utils import human_delay, log
from . import answers
from .search import JobCard

SEL_DESCRIPTION = "div.jobs-description__content, article.jobs-description__container, #job-details"

# --- Selectors (update these if LinkedIn changes its markup) -----------------
SEL_EASY_APPLY_BTN = "button.jobs-apply-button"  # legacy UI only; see _easy_apply_button
SEL_MODAL = "div.jobs-easy-apply-modal, div[data-test-modal], div[role='dialog']"
SEL_FORM_ELEMENT = "div.fb-dash-form-element, div[data-test-form-element], div.jobs-easy-apply-form-section__grouping"
SEL_NEXT = "button[aria-label='Continue to next step']"
SEL_REVIEW = "button[aria-label='Review your application']"
SEL_SUBMIT = "button[aria-label='Submit application']"
SEL_DISCARD = "button[data-control-name='discard_application_confirm_btn'], button[data-test-dialog-secondary-btn]"
SEL_CLOSE = "button[aria-label='Dismiss']"
SEL_FORM_ERROR = "div.artdeco-inline-feedback--error"


class ApplyResult:
    APPLIED = "applied"
    DRY_RUN = "dry_run"
    SKIPPED = "skipped"
    EXTERNAL = "external"
    FAILED = "failed"
    LIMIT = "limit"      # LinkedIn's own daily Easy Apply limit — stop the run


def blocklisted(card: JobCard, cfg: dict) -> str | None:
    words = [w.lower() for w in cfg["search"].get("blocklist_keywords", [])]
    haystack = f"{card.title} {card.company} {card.description}".lower()
    for w in words:
        if w and w in haystack:
            return w
    return None


def open_and_apply(page: Page, card: JobCard, cfg: dict, delays: list[float]) -> tuple[str, str]:
    """Return (status, reason)."""
    # LinkedIn keyword search returns loosely-related roles; don't burn the
    # daily cap on titles that aren't actually relevant.
    req = [w.lower() for w in cfg["search"].get("required_title_keywords", [])]
    if req and not any(w in card.title.lower() for w in req):
        return ApplyResult.SKIPPED, "title doesn't match required_title_keywords"

    # Title-level blocklist (word-boundary): role types the user doesn't want,
    # without excluding jobs that merely mention the term in the description.
    for w in cfg["search"].get("blocklist_title_keywords", []):
        if re.search(rf"\b{re.escape(w)}\b", card.title, re.I):
            return ApplyResult.SKIPPED, f"title matches blocklisted role type '{w}'"

    page.goto(card.url, wait_until="domcontentloaded")
    human_delay(delays)
    from .auth import _dismiss_cookie_banner
    _dismiss_cookie_banner(page)

    # Capture the description so we can (a) blocklist-check and (b) tailor the letter.
    card.description = _read_description(page)
    if not card.company:
        card.company = _company_from_page(page)

    blocked = blocklisted(card, cfg)
    if blocked:
        return ApplyResult.SKIPPED, f"blocklisted keyword '{blocked}'"

    easy_btn = _easy_apply_button(page)
    if easy_btn is None:
        # No button also happens when the job was ALREADY applied to (e.g.
        # manually) — the page then shows a status timeline. UI may be localised.
        already = page.get_by_text(re.compile(
            r"application status|application submitted|ansökningsstatus|ansökan har skickats",
            re.I))
        if already.count():
            return ApplyResult.APPLIED, "already applied (status timeline on job page)"
        return ApplyResult.EXTERNAL, "no Easy Apply (external application)"

    # Pre-generate the tailored cover letter (cached on disk) before the form opens.
    cover_text = cover_letter.generate(card, cfg)
    cover_pdf = (
        cover_letter.render_pdf(cover_text, card.job_id, cfg)
        if cover_text and cfg.get("cover_letter", {}).get("upload_pdf")
        else None
    )
    # Tailored CV for this JD (falls back to the static resume_path).
    resume_pdf = resume_mod.resume_for_job(card, cfg)

    easy_btn.click()
    human_delay(delays)

    # LinkedIn throttles Easy Apply (~35/day); when hit, applying is pointless
    # until tomorrow — tell the caller to end the whole run.
    if page.get_by_text(re.compile(r"(easy apply|application) limit", re.I)).count():
        return ApplyResult.LIMIT, "LinkedIn daily Easy Apply limit reached"

    try:
        page.wait_for_selector(SEL_MODAL, timeout=10_000)
    except PWTimeout:
        # The click sometimes doesn't register on the new UI — try once more.
        easy_btn = _easy_apply_button(page)
        if easy_btn is not None:
            easy_btn.click()
        try:
            page.wait_for_selector(SEL_MODAL, timeout=10_000)
        except PWTimeout:
            _screenshot(page, card, cfg, suffix="fail-no-modal")
            return ApplyResult.FAILED, "Easy Apply modal did not open"

    return _drive_modal(page, card, cfg, delays, cover_text, cover_pdf, resume_pdf)


def _easy_apply_button(page: Page):
    """Find the Easy Apply control on either the legacy or the new job page UI.

    The new UI renders it as an <a> with obfuscated classes, so match by
    accessible name instead of CSS class. Returns a clickable locator or None.
    """
    legacy = page.locator(SEL_EASY_APPLY_BTN)
    if legacy.count():
        return legacy.first
    name = re.compile(r"^\s*easy apply", re.I)
    for role in ("button", "link"):
        btn = page.get_by_role(role, name=name)
        if btn.count() and btn.first.is_visible():
            return btn.first
    return None


def _read_description(page: Page) -> str:
    loc = page.locator(SEL_DESCRIPTION)
    if loc.count() == 0:
        return ""
    return (loc.first.inner_text() or "").strip()


def _company_from_page(page: Page) -> str:
    """Company name from the job page top card (search cards in the new UI
    often have obfuscated classes and yield an empty company)."""
    loc = page.locator(
        "div.job-details-jobs-unified-top-card__company-name a, a[href*='/company/']"
    )
    for i in range(min(loc.count(), 5)):
        try:
            text = (loc.nth(i).inner_text() or "").strip().split("\n")[0]
        except Exception:
            continue
        if text:
            return text
    return ""


def _drive_modal(page: Page, card: JobCard, cfg: dict, delays: list[float],
                 cover_text: str | None = None, cover_pdf: str | None = None,
                 resume_pdf: str | None = None) -> tuple[str, str]:
    dry_run = cfg["safety"].get("dry_run", True)
    max_steps = 12  # guard against infinite loops

    for _ in range(max_steps):
        unanswered = _fill_visible_fields(page, cfg, cover_text, cover_pdf, resume_pdf)
        if unanswered:
            _discard(page)
            return ApplyResult.SKIPPED, f"unanswered required question: '{unanswered}'"

        human_delay(delays)

        submit = _modal_button(page, SEL_SUBMIT, r"^submit")
        if submit is not None:
            return _finish(page, card, cfg, dry_run, delays, submit)

        nxt = _modal_button(page, SEL_REVIEW, r"^review") \
            or _modal_button(page, SEL_NEXT, r"^(next|continue)")
        if nxt is None:
            if page.locator(SEL_MODAL).count() == 0:
                return ApplyResult.FAILED, "modal closed unexpectedly"
            _screenshot(page, card, cfg, suffix="fail-no-next")
            _discard(page)
            return ApplyResult.FAILED, "no Next/Review/Submit button found"
        nxt.click()
        human_delay(delays)

        # If LinkedIn rejected a value we filled (wrong format etc.), the step
        # doesn't advance and an inline error appears — fix numeric-format
        # rejections once, otherwise skip instead of looping.
        err = page.locator(SEL_FORM_ERROR)
        if err.count() and _fix_numeric_errors(page, cfg):
            nxt.click()
            human_delay(delays)
        if err.count():
            msg = (err.first.inner_text() or "").strip().splitlines()[0]
            _discard(page)
            return ApplyResult.SKIPPED, f"form validation error: '{msg}'"

    _discard(page)
    return ApplyResult.FAILED, "exceeded max form steps"


# LinkedIn's localized "enter a number" validation messages.
_NUMERIC_ERR = re.compile(
    r"decimal|number|número|numero|zahl|liczb|nombre|whole|entero|inteiro|siffr",
    re.I,
)


def _fix_numeric_errors(page: Page, cfg: dict) -> bool:
    """Re-fill inputs whose inline error demands a number (our label-based
    guess was free text). Returns True if anything was corrected."""
    fixed = False
    groups = page.locator(SEL_FORM_ELEMENT)
    for i in range(groups.count()):
        grp = groups.nth(i)
        err = grp.locator(SEL_FORM_ERROR)
        if not err.count() or not _NUMERIC_ERR.search(err.first.inner_text() or ""):
            continue
        inp = grp.locator("input[type=text], input[type=number], input:not([type])")
        if not inp.count():
            continue
        label = _group_label(grp)
        ans = cfg["answers"]
        value = answers.salary_answer(label, ans) or answers.numeric_answer(label, ans)
        if value is None:
            continue
        inp.first.fill(str(value))
        log.info("[fix-validation] %r -> %r", label[:70], value)
        fixed = True
    return fixed


def _finish(page: Page, card: JobCard, cfg: dict, dry_run: bool, delays: list[float],
            submit: Locator) -> tuple[str, str]:
    # Unfollow the company checkbox is often pre-checked; leave it, harmless.
    if dry_run:
        _screenshot(page, card, cfg, suffix="dryrun")
        _discard(page)
        return ApplyResult.DRY_RUN, "form completed; not submitted (dry_run)"

    submit.click()
    human_delay([2, 4])
    # Verify the submit actually went through before recording 'applied'.
    err = page.locator(SEL_FORM_ERROR)
    if err.count():
        msg = (err.first.inner_text() or "").strip().splitlines()[0]
        _screenshot(page, card, cfg, suffix="fail-submit")
        _discard(page)
        return ApplyResult.SKIPPED, f"form validation error: '{msg}'"
    if _modal_button(page, SEL_SUBMIT, r"^submit") is not None:
        _screenshot(page, card, cfg, suffix="fail-submit")
        _discard(page)
        return ApplyResult.FAILED, "submit click did not go through"
    _screenshot(page, card, cfg, suffix="submitted")
    # Close the post-submit confirmation dialog.
    if page.locator(SEL_CLOSE).count():
        page.locator(SEL_CLOSE).first.click()
    return ApplyResult.APPLIED, ""


def _modal_button(page: Page, css: str, name: str) -> Locator | None:
    """CSS first (English aria-labels), then accessible name within the modal —
    survives aria-label changes in the new UI."""
    loc = page.locator(css)
    if loc.count():
        return loc.first
    scope = page.locator(SEL_MODAL).first if page.locator(SEL_MODAL).count() else page
    btn = scope.get_by_role("button", name=re.compile(name, re.I))
    if btn.count():
        return btn.first
    return None


def _fill_visible_fields(page: Page, cfg: dict, cover_text: str | None = None,
                         cover_pdf: str | None = None, resume_pdf: str | None = None) -> str | None:
    """Fill every form element in the current modal step.

    Returns the label of the first REQUIRED field we couldn't answer, or None.
    """
    ans = cfg["answers"]
    applicant = cfg["applicant"]
    groups = page.locator(SEL_FORM_ELEMENT)

    for i in range(groups.count()):
        grp = groups.nth(i)
        label = _group_label(grp)
        required = _is_required(grp)

        # 0) file uploads (resume / cover letter)
        file_input = grp.locator("input[type=file]")
        if file_input.count():
            _handle_file_upload(file_input.first, label, cfg, cover_pdf, resume_pdf)
            continue

        # 1) text / number inputs (no type attribute defaults to text)
        text_input = grp.locator(
            "input[type=text], input[type=email], input[type=tel], input[type=number], input:not([type])"
        )
        if text_input.count():
            if text_input.first.input_value().strip():
                continue  # already filled (LinkedIn pre-fills from profile)
            value = _resolve_text(label, ans, applicant)
            if value is None and required:
                value = llm_answer(label, None, cfg)
            if value is None and required and ans.get("always_fill"):
                itype = (text_input.first.get_attribute("type") or "text").lower()
                # Numeric-validated inputs reject free text even with type=text.
                if itype == "number" or answers.looks_numeric_question(label):
                    value = answers.numeric_answer(label, ans)
                if value is None:
                    value = ans.get("text_fallback", "Please see my CV — happy to elaborate.")
                log.info("[last-resort] %r -> %r", label[:70], str(value)[:60])
            if value is None:
                if required:
                    return label
                continue
            text_input.first.fill(str(value))
            _commit_typeahead(page, text_input.first)
            continue

        # 2) textarea — cover-letter / motivation boxes get the tailored letter
        textarea = grp.locator("textarea")
        if textarea.count():
            if textarea.first.input_value().strip():
                continue
            if cover_text and _is_cover_letter_field(label):
                textarea.first.fill(cover_text)
                continue
            value = answers.text_answer(label, ans)
            if value is None and required:
                value = llm_answer(label, None, cfg)
            if value is None and required and ans.get("always_fill"):
                value = ans.get("text_fallback", "Please see my CV — happy to elaborate.")
                log.info("[last-resort] %r -> %r", label[:70], str(value)[:60])
            if value is None:
                if required:
                    return label
                continue
            textarea.first.fill(str(value))
            continue

        # 3) <select> dropdown
        select = grp.locator("select")
        if select.count():
            options = [o.strip() for o in select.first.locator("option").all_inner_texts()]
            real = [o for o in options if o and not _is_placeholder(o)]
            choice = answers.dropdown_answer(label, real, ans)
            if choice is None:
                # Try profile data (phone country code, city, ...) against options.
                want = _resolve_text(label, ans, applicant)
                if want:
                    want_l = str(want).lower()
                    choice = next((o for o in real if want_l in o.lower() or o.lower() in want_l), None)
            # Recognise common pickers by the SHAPE of their options, so
            # localised labels ("Código do país") don't matter.
            if choice is None and real and all("@" in o for o in real):
                # Email picker — options are the account's own verified emails.
                choice = real[0]
            if choice is None and any("(+" in o for o in real):
                # Match by dial code digits ("+1") — works even when the country
                # name is localised ("Estados Unidos (+1)").
                m = re.search(r"\+\d+", applicant.get("phone_country_code") or "")
                if m:
                    code = m.group(0)
                    choice = next((o for o in real if f"({code})" in o or o.endswith(code)), None)
            if choice is None:
                choice = answers.language_level_answer(label, real)
            if choice is None and required:
                choice = llm_answer(label, real, cfg)
            if choice is None and required and ans.get("default_positive"):
                choice = _positive_option(real)
                if choice:
                    log.info("[default-positive] %r -> %r", label[:70], choice)
            if choice is None and required and ans.get("always_fill") and real:
                choice = real[0]
                log.info("[last-resort] %r -> first option %r", label[:70], choice)
            if choice is None:
                if required:
                    return label
                continue
            select.first.select_option(label=choice)
            continue

        # 4) radio button group (Yes/No etc.)
        radios = grp.locator("input[type=radio]")
        if radios.count():
            if _select_radio(grp, label, ans):
                continue
            if required:
                opts = _radio_option_texts(grp)
                pick = answers.language_level_answer(label, opts) if opts else None
                if pick is None and opts:
                    pick = llm_answer(label, opts, cfg)
                if pick is None and opts and ans.get("default_positive"):
                    pick = _positive_option(opts)
                    if pick:
                        log.info("[default-positive] %r -> %r", label[:70], pick)
                if pick is None and opts and ans.get("always_fill"):
                    pick = opts[0]
                    log.info("[last-resort] %r -> first option %r", label[:70], pick)
                if pick and _check_radio_by_text(grp, pick):
                    continue
                return label
            continue

        # 5) lone checkbox (consent/agree) — tick if required
        checkbox = grp.locator("input[type=checkbox]")
        if checkbox.count() == 1 and required and not checkbox.first.is_checked():
            try:
                checkbox.first.check(timeout=5000)
            except Exception:
                # LinkedIn hides the real input behind a styled label —
                # check() waits for visibility forever; click the label instead.
                rid = checkbox.first.get_attribute("id")
                lab = grp.locator(f"label[for='{rid}']") if rid else None
                if lab is not None and lab.count():
                    lab.first.click()
            continue

    return None


def _commit_typeahead(page: Page, field) -> None:
    """If filling opened a typeahead suggestion list, pick the first suggestion.

    The open list otherwise covers the Next button and blocks the click.
    """
    try:
        page.wait_for_timeout(800)
        hits = page.locator(
            "[data-test-single-typeahead-entity-form-search-result], .search-typeahead-v2__hit"
        )
        if hits.count():
            field.press("ArrowDown")
            field.press("Enter")
    except Exception as exc:
        log.debug("typeahead commit skipped: %s", exc)


def _resolve_text(label: str, ans: dict, applicant: dict) -> str | None:
    # Normalise hyphens so "E-mail" matches "email" etc.
    label_l = label.lower().replace("-", "")
    # Map obvious profile fields first.
    if "first name" in label_l or "given name" in label_l:
        return applicant.get("first_name")
    if "last name" in label_l or "surname" in label_l or "family name" in label_l:
        return applicant.get("last_name")
    # "Full name" / bare "Name" / "Legal name" -> first + last (but never match
    # "company name", "first/last name" — those are handled above / below).
    if "full name" in label_l or "legal name" in label_l or label_l.strip() in (
        "name", "name *", "your name", "candidate name", "applicant name",
    ):
        parts = [applicant.get("first_name"), applicant.get("last_name")]
        full = " ".join(p for p in parts if p)
        if full:
            return full
    if "email" in label_l:                       # before "address" (email address)
        return applicant.get("email")
    if "phone" in label_l or "mobile" in label_l:
        return applicant.get("phone")
    if ("post" in label_l and "code" in label_l) or "postcode" in label_l or "zip" in label_l:
        return applicant.get("postal_code") or None
    if "country" in label_l and "code" not in label_l:
        return applicant.get("country") or None
    if "address" in label_l or "street" in label_l:
        return applicant.get("address") or applicant.get("city")
    if "city" in label_l or "town" in label_l or "location" in label_l:
        return applicant.get("city")
    if "linkedin" in label_l:
        return applicant.get("linkedin_url")
    sal = answers.salary_answer(label, ans)
    if sal is not None:
        return sal
    return answers.text_answer(label, ans)


_COVER_LETTER_HINTS = ("cover letter", "motivation", "why are you", "why do you want",
                       "message to", "tell us", "anything else", "additional information")


def _is_cover_letter_field(label: str) -> bool:
    label_l = label.lower()
    return any(h in label_l for h in _COVER_LETTER_HINTS)


def _handle_file_upload(file_input, label: str, cfg: dict, cover_pdf: str | None,
                        resume_pdf: str | None = None) -> None:
    """Attach the resume (tailored per-JD if enabled) or, if asked and available,
    the cover-letter PDF.

    LinkedIn usually pre-attaches the most recent resume; we only set a file when
    the input is empty, to avoid disturbing an existing upload.
    """
    label_l = label.lower()
    is_cover = "cover" in label_l or "letter" in label_l
    try:
        if is_cover and cover_pdf:
            file_input.set_input_files(cover_pdf)
        elif not is_cover:
            resume = resume_pdf or cfg["applicant"].get("resume_path")
            if resume:
                file_input.set_input_files(resume)
    except Exception as exc:
        log.debug("file upload (%s) skipped: %s", label, exc)


def _select_radio(grp: Locator, label: str, ans: dict) -> bool:
    yn = answers.yes_no_answer(label, ans)
    want = answers.text_answer(label, ans)  # text-driven match (e.g. dropdown-style radios)
    radios = grp.locator("input[type=radio]")
    for i in range(radios.count()):
        radio = radios.nth(i)
        rid = radio.get_attribute("id")
        text = ""
        if rid:
            lab = grp.locator(f"label[for='{rid}']")
            if lab.count():
                text = (lab.first.inner_text() or "").strip().lower()
        if yn is not None and text == ("yes" if yn else "no"):
            _check_control(grp, radio, rid)
            return True
        if want and want.lower() in text:
            _check_control(grp, radio, rid)
            return True
    return False


# Affirmative option labels across the languages we encounter.
_POSITIVE = {"yes", "sí", "si", "sim", "ja", "oui", "sì", "da", "true"}

# "Select an option" placeholders across languages — never valid answers.
_PLACEHOLDER_PREFIXES = ("select", "selecion", "seleccion", "sélection", "wähle",
                         "wybierz", "selezion", "välj", "alege", "kies", "choose",
                         "vælg", "velg", "--")
# German puts the verb last ("Option auswählen"), so prefixes miss it.
_PLACEHOLDER_WORDS = ("auswählen", "auswahl")


def _is_placeholder(option: str) -> bool:
    o = option.lower()
    return o.startswith(_PLACEHOLDER_PREFIXES) or any(w in o for w in _PLACEHOLDER_WORDS)


def _positive_option(options: list[str]) -> str | None:
    """Return the affirmative option if this looks like a Yes/No question."""
    return next((o for o in options if o.strip().lower() in _POSITIVE), None)


def _check_control(grp: Locator, control, rid: str | None) -> None:
    """check() a radio/checkbox, falling back to clicking its label when the
    input is visually hidden behind LinkedIn's styled controls."""
    try:
        control.check(timeout=5000)
    except Exception:
        lab = grp.locator(f"label[for='{rid}']") if rid else None
        if lab is not None and lab.count():
            lab.first.click()
        else:
            raise


def _radio_option_texts(grp: Locator) -> list[str]:
    out = []
    radios = grp.locator("input[type=radio]")
    for i in range(radios.count()):
        rid = radios.nth(i).get_attribute("id")
        if rid:
            lab = grp.locator(f"label[for='{rid}']")
            if lab.count():
                text = (lab.first.inner_text() or "").strip()
                if text:
                    out.append(text)
    return out


def _check_radio_by_text(grp: Locator, wanted: str) -> bool:
    radios = grp.locator("input[type=radio]")
    for i in range(radios.count()):
        radio = radios.nth(i)
        rid = radio.get_attribute("id")
        if not rid:
            continue
        lab = grp.locator(f"label[for='{rid}']")
        if lab.count() and (lab.first.inner_text() or "").strip().lower() == wanted.strip().lower():
            _check_control(grp, radio, rid)
            return True
    return False


def _group_label(grp: Locator) -> str:
    lbl = grp.locator("label, legend, span.fb-dash-form-element__label")
    if lbl.count():
        return _clean_label(lbl.first.inner_text() or "")
    return (grp.inner_text() or "").strip().split("\n")[0]


def _clean_label(text: str) -> str:
    """Labels repeat their text in a visually-hidden span (and may append a
    bare 'Required' line) — keep each distinct line once."""
    seen: set[str] = set()
    out: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        key = line.lower()
        if not line or key in seen or key in ("required", "*"):
            continue
        seen.add(key)
        out.append(line)
    return " ".join(out)


def _is_required(grp: Locator) -> bool:
    text = (grp.inner_text() or "")
    if "*" in text or "required" in text.lower():
        return True
    return grp.locator("[aria-required='true'], [required]").count() > 0


def _discard(page: Page) -> None:
    """Close the modal without applying, dismissing the 'discard?' dialog."""
    try:
        close = _modal_button(page, SEL_CLOSE, r"dismiss|close")
        if close is not None:
            close.click()
            human_delay([0.5, 1.2])
        disc = _modal_button(page, SEL_DISCARD, r"discard")
        if disc is not None:
            disc.click()
    except Exception as exc:
        log.debug("discard cleanup issue: %s", exc)


def _screenshot(page: Page, card: JobCard, cfg: dict, suffix: str) -> None:
    d = Path(cfg["storage"].get("screenshot_dir", "data/screenshots"))
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{card.job_id}-{suffix}.png"
    try:
        page.screenshot(path=str(path))
        log.info("Saved screenshot %s", path)
    except Exception as exc:
        log.debug("screenshot failed: %s", exc)
