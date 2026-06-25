"""Apply on the company's own ATS when a job has no Easy Apply button.

open_and_apply() returns EXTERNAL for those jobs; main.py then calls
apply_external(), which clicks the plain Apply button (it usually opens a new
tab), classifies the ATS by the final URL's domain, and — for tractable boards
(Greenhouse, Lever, Ashby, Teamtailor, Recruitee, Workable) plus small unknown
forms with a CV upload — fills the application form with the SAME answer stack
as Easy Apply and submits.

Anything requiring an account, a CAPTCHA, or an email-verification code is
deferred: we screenshot, return EXTERNAL with a precise reason, and the job
stays logged for manual follow-up (v1 policy).

Safety rails:
  * never create accounts — any visible password field counts as a login wall;
  * never bypass CAPTCHAs (an invisible reCAPTCHA badge that solves itself is
    tolerated; a visible challenge is not);
  * ~120s wall-clock budget per attempt;
  * whatever happens, the ATS tab is closed and the LinkedIn `page` object is
    restored before returning, so the main loop continues unharmed;
  * honors safety.dry_run exactly like Easy Apply (fill, screenshot, no submit).
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import Locator, Page, TimeoutError as PWTimeout

from .. import cover_letter
from .. import resume as resume_mod
from ..llm_answers import llm_answer
from ..utils import human_delay, log
from . import answers
from .easy_apply import (
    ApplyResult,
    _clean_label,
    _is_cover_letter_field,
    _is_placeholder,
    _positive_option,
    _resolve_text,
    _screenshot,
)
from .search import JobCard

TIME_BUDGET_S = 120          # hard wall-clock cap per external attempt
DEFAULT_MAX_FIELDS = 25      # config: external_apply.max_fields

# --- ATS classification -------------------------------------------------------
# Boards our generic filler is known to handle: a single native-HTML form,
# no account needed.
TRACTABLE_ATS = {
    "greenhouse": ("boards.greenhouse.io", "job-boards.greenhouse.io"),
    "lever": ("jobs.lever.co",),
    "ashby": ("jobs.ashbyhq.com",),
    "teamtailor": (".teamtailor.com",),
    "recruitee": (".recruitee.com",),
    "workable": ("apply.workable.com",),
}
# Account-based / multi-step wizard ATSes — deferred to manual in v1.
DEFERRED_ATS = {
    "workday": ("myworkdayjobs.com",),   # plus any host containing "workday"
    "smartrecruiters": ("smartrecruiters.com",),
    "icims": ("icims.com",),
    "successfactors": ("successfactors.com",),
    "taleo": ("taleo.net",),
}


def classify_ats(url: str) -> tuple[str, str]:
    """Classify an ATS by URL: ('tractable' | 'deferred' | 'unknown', ats name)."""
    host = (urlparse(url).hostname or "").lower()
    if "workday" in host:  # *.workday.com, *.myworkdayjobs.com, regional variants
        return "deferred", "workday"
    for table, kind in ((TRACTABLE_ATS, "tractable"), (DEFERRED_ATS, "deferred")):
        for ats, patterns in table.items():
            if any(_host_match(host, p) for p in patterns):
                return kind, ats
    return "unknown", host or "unknown"


def _host_match(host: str, pattern: str) -> bool:
    if pattern.startswith("."):           # ".teamtailor.com" == *.teamtailor.com
        return host.endswith(pattern) or host == pattern[1:]
    return host == pattern or host.endswith("." + pattern)


# --- Label cleaning -------------------------------------------------------------
_LABEL_NOISE = re.compile(
    r"\s*(\(optional\)|\(required\)|\(valfri\w*\)|\(obligatorisk\w*\)|[*✱✶])\s*$",
    re.I,
)


def clean_label(text: str) -> str:
    """easy_apply._clean_label plus ATS-style '(optional)' / trailing-* removal."""
    cleaned = _clean_label(text or "")
    prev = None
    while prev != cleaned:
        prev = cleaned
        cleaned = _LABEL_NOISE.sub("", cleaned).strip()
    return cleaned


# --- Answer resolution (pure; unit-tested without a browser) -------------------
@dataclass
class FieldSpec:
    """Browser-free description of one form field."""
    label: str
    kind: str                      # text | textarea | select | radio | checkbox
    input_type: str = "text"       # for kind == "text": text/email/tel/url/number
    options: list[str] = field(default_factory=list)
    required: bool = False


def resolve_answer(spec: FieldSpec, cfg: dict, llm=llm_answer):
    """The Easy Apply answer stack, ported to a generic ATS field.

    Tier order (same as easy_apply): applicant fields + config maps (salary,
    text, experience_years, yes_no) -> llm_answers.llm_answer (REQUIRED only)
    -> default_positive (choice fields) -> always_fill last resort.

    Returns the text/option to fill, True for "tick this checkbox", or None
    (= leave blank; the caller skips optional fields and defers on required).
    """
    ans = cfg["answers"]

    if spec.kind == "checkbox":
        # Consent/agree boxes: tick when required, never volunteer otherwise.
        return True if spec.required else None

    if spec.kind in ("select", "radio"):
        return _resolve_choice(spec, cfg, llm)

    # text inputs and textareas --------------------------------------------------
    applicant = cfg["applicant"]
    label = spec.label
    if spec.kind == "textarea":
        value = answers.text_answer(label, ans)
    else:
        value = _resolve_text(label, ans, applicant)
        if value is None:
            value = _type_hint_answer(spec, applicant)
    if value is None and spec.required:
        value = llm(label, None, cfg)
    if value is None and spec.required and ans.get("always_fill"):
        if spec.input_type == "number" or answers.looks_numeric_question(label):
            value = answers.numeric_answer(label, ans)
        if value is None and spec.input_type != "number":
            value = ans.get("text_fallback", "Please see my CV — happy to elaborate.")
        if value is not None:
            log.info("[external last-resort] %r -> %r", label[:70], str(value)[:60])
    return value


def _type_hint_answer(spec: FieldSpec, applicant: dict) -> str | None:
    """Bare ATS fields often carry their meaning in the input type, not the label."""
    label_l = spec.label.lower()
    if spec.input_type == "email":
        return applicant.get("email")
    if spec.input_type == "tel":
        return applicant.get("phone")
    if spec.input_type == "url" and (
        not label_l or any(w in label_l for w in ("linkedin", "profile", "website", "portfolio"))
    ):
        return applicant.get("linkedin_url")
    return None


def _resolve_choice(spec: FieldSpec, cfg: dict, llm) -> str | None:
    """Select/radio chain — mirrors the <select> branch of _fill_visible_fields."""
    ans, applicant = cfg["answers"], cfg["applicant"]
    label = spec.label
    real = [o.strip() for o in spec.options if o.strip() and not _is_placeholder(o)]
    if not real:
        return None
    choice = answers.dropdown_answer(label, real, ans)
    if choice is None:
        want = _resolve_text(label, ans, applicant)
        if want:
            want_l = str(want).lower()
            choice = next((o for o in real if want_l in o.lower() or o.lower() in want_l), None)
    if choice is None and any("(+" in o for o in real):
        m = re.search(r"\+\d+", applicant.get("phone_country_code") or "")
        if m:
            code = m.group(0)
            choice = next((o for o in real if f"({code})" in o or o.endswith(code)), None)
    if choice is None:
        choice = answers.language_level_answer(label, real)
    if choice is None and spec.required:
        choice = llm(label, real, cfg)
    if choice is None and spec.required and ans.get("default_positive"):
        choice = _positive_option(real)
        if choice:
            log.info("[external default-positive] %r -> %r", label[:70], choice)
    if choice is None and spec.required and ans.get("always_fill"):
        choice = real[0]
        log.info("[external last-resort] %r -> first option %r", label[:70], choice)
    return choice


# --- Entry point ----------------------------------------------------------------
def apply_external(page: Page, card: JobCard, cfg: dict, delays: list[float]) -> tuple[str, str]:
    """Attempt the application on the company's ATS. Returns (ApplyResult.*, reason).

    Whatever happens, the ATS tab is closed and `page` (the LinkedIn tab) is
    usable when this returns.
    """
    deadline = time.monotonic() + TIME_BUDGET_S
    ats_page: Page | None = None
    try:
        btn = _plain_apply_button(page)
        if btn is None:
            return ApplyResult.EXTERNAL, "no external Apply button on the job page"
        ats_page = _open_ats_page(page, btn)
        if ats_page is None:
            return ApplyResult.EXTERNAL, "Apply click did not open an application page"
        if ats_page is not page:
            ats_page.set_default_timeout(15_000)
        return _run_ats_flow(ats_page, card, cfg, delays, deadline)
    except PWTimeout:
        return ApplyResult.EXTERNAL, "external attempt timed out"
    except Exception as exc:
        log.exception("External apply error on %s", card.job_id)
        return ApplyResult.FAILED, f"external apply error: {exc}"
    finally:
        _restore_linkedin(page, ats_page)


_APPLY_NAME = re.compile(r"^\s*(apply\b|ansök)", re.I)


def _plain_apply_button(page: Page) -> Locator | None:
    """The non-Easy-Apply Apply control ('Apply', 'Apply on company website', …)."""
    for role in ("button", "link"):
        loc = page.get_by_role(role, name=_APPLY_NAME)
        for i in range(min(loc.count(), 5)):
            btn = loc.nth(i)
            try:
                if btn.is_visible():
                    return btn
            except Exception:
                continue
    return None


def _open_ats_page(page: Page, btn: Locator) -> Page | None:
    """Click Apply; return the ATS page (new tab, or `page` itself on same-tab nav)."""
    origin = page.url
    try:
        with page.context.expect_page(timeout=10_000) as pinfo:
            btn.click()
        ats = pinfo.value
    except PWTimeout:
        # No new tab — maybe a same-tab navigation.
        try:
            page.wait_for_load_state("domcontentloaded", timeout=10_000)
        except PWTimeout:
            pass
        if page.url != origin and "linkedin.com/jobs" not in page.url:
            return page
        return None
    try:
        ats.wait_for_load_state("domcontentloaded", timeout=20_000)
    except PWTimeout:
        pass
    return ats


def _run_ats_flow(p: Page, card: JobCard, cfg: dict, delays: list[float],
                  deadline: float) -> tuple[str, str]:
    url = _settle(p, deadline)
    kind, ats = classify_ats(url)
    log.info("[external] %s -> %s ATS (%s): %s", card.job_id, kind, ats, url)
    if kind == "deferred":
        return ApplyResult.EXTERNAL, f"deferred ATS ({ats}) — apply manually"

    _dismiss_ats_cookies(p)
    blocked = _blocking_gate(p, ats)
    if blocked:
        _screenshot(p, card, cfg, suffix="external-blocked")
        return ApplyResult.EXTERNAL, blocked

    form = _find_application_form(p, deadline)
    if form is None:
        return ApplyResult.EXTERNAL, f"no application form found ({ats})"
    if kind == "unknown" and not form.locator("input[type=file]").count():
        return ApplyResult.EXTERNAL, f"unknown ATS without a CV upload ({ats})"
    max_fields = int(cfg.get("external_apply", {}).get("max_fields", DEFAULT_MAX_FIELDS))
    n_fields = _count_visible_fields(form)
    if n_fields > max_fields:
        return ApplyResult.EXTERNAL, \
            f"form too large ({n_fields} fields > max_fields={max_fields}) ({ats})"

    unanswered = _fill_form(p, form, card, cfg, delays, deadline)
    if time.monotonic() > deadline:
        return ApplyResult.EXTERNAL, f"external attempt exceeded time budget ({ats})"
    if unanswered:
        _screenshot(p, card, cfg, suffix="external-blocked")
        return ApplyResult.EXTERNAL, f"required field unanswered: '{unanswered}' ({ats})"
    # Filling may have revealed a login / verification / captcha step.
    blocked = _blocking_gate(p, ats)
    if blocked:
        _screenshot(p, card, cfg, suffix="external-blocked")
        return ApplyResult.EXTERNAL, blocked

    if cfg["safety"].get("dry_run", True):
        _screenshot(p, card, cfg, suffix="external-dryrun")
        return ApplyResult.DRY_RUN, f"external form completed; not submitted (dry_run) ({ats})"

    return _submit(p, form, card, cfg, deadline, ats)


def _settle(p: Page, deadline: float) -> str:
    """Wait out redirect chains (LinkedIn interstitial -> careers page -> ATS)."""
    last = None
    while time.monotonic() < deadline:
        try:
            p.wait_for_load_state("domcontentloaded", timeout=10_000)
        except PWTimeout:
            pass
        if p.url == last:
            break
        last = p.url
        p.wait_for_timeout(1_500)
    return p.url


# --- Gates: things we refuse to automate past ----------------------------------
SEL_CAPTCHA = (
    "iframe[src*='captcha'], .g-recaptcha, [data-hcaptcha], .h-captcha, "
    ".cf-turnstile, iframe[src*='turnstile'], iframe[src*='challenges.cloudflare.com']"
)
_VERIFY_TEXT = re.compile(
    r"security code|verif(y|ication) (your )?e-?mail|verification code"
    r"|enter the code|verifiera din e-post|verifieringskod",
    re.I,
)


def _blocking_gate(p: Page, ats: str) -> str | None:
    """Return a precise EXTERNAL reason if a no-go gate is on screen, else None."""
    if _login_wall(p):
        return f"login wall ({ats})"
    if _verification_gate(p):
        return f"email verification required ({ats})"
    if _captcha_blocking(p):
        return f"CAPTCHA present ({ats})"
    return None


def _login_wall(p: Page) -> bool:
    """Any visible password field = an account is needed. We never create accounts."""
    loc = p.locator("input[type=password]")
    for i in range(min(loc.count(), 5)):
        try:
            if loc.nth(i).is_visible():
                return True
        except Exception:
            continue
    return False


def _verification_gate(p: Page) -> bool:
    try:
        loc = p.get_by_text(_VERIFY_TEXT)
        for i in range(min(loc.count(), 5)):
            if loc.nth(i).is_visible():
                return True
    except Exception:
        pass
    return False


def _captcha_blocking(p: Page) -> bool:
    """A captcha the user would have to solve. An invisible-reCAPTCHA badge that
    solves itself is tolerated (ignoring it is not bypassing); a visible
    challenge/checkbox widget defers the job."""
    loc = p.locator(SEL_CAPTCHA)
    for i in range(min(loc.count(), 10)):
        el = loc.nth(i)
        try:
            if not el.is_visible():
                continue
            meta = (el.get_attribute("data-size") or "") + (el.get_attribute("src") or "")
            if "invisible" in meta:
                continue
            box = el.bounding_box()
            if box and box["height"] < 70:   # the corner badge strip is ~60px
                continue
            return True
        except Exception:
            continue
    return False


# --- Form discovery -------------------------------------------------------------
SEL_FIELDS = (
    "input:not([type=hidden]):not([type=submit]):not([type=button])"
    ":not([type=image]):not([type=reset]), select, textarea"
)


def _find_application_form(p: Page, deadline: float) -> Locator | None:
    form = _pick_form(p)
    if form is not None:
        return form
    # Landing pages (Lever 'Apply for this job', Ashby/Teamtailor 'Apply')
    # need one more click to reach the actual form.
    btn = _ats_apply_link(p)
    if btn is not None and time.monotonic() < deadline:
        try:
            btn.click()
            _settle(p, deadline)
        except Exception as exc:
            log.debug("ATS apply click failed: %s", exc)
        return _pick_form(p)
    return None


def _pick_form(p: Page) -> Locator | None:
    """Prefer the form holding a file upload (the application form); else any
    form with a few visible fields."""
    forms = p.locator("form")
    best = None
    for i in range(min(forms.count(), 10)):
        f = forms.nth(i)
        try:
            if f.locator("input[type=file]").count():
                return f
            if best is None and _count_visible_fields(f) >= 3:
                best = f
        except Exception:
            continue
    return best


def _ats_apply_link(p: Page) -> Locator | None:
    name = re.compile(r"^\s*(apply (for|now|to|here)|apply$|ansök)", re.I)
    for role in ("link", "button"):
        loc = p.get_by_role(role, name=name)
        for i in range(min(loc.count(), 5)):
            try:
                if loc.nth(i).is_visible():
                    return loc.nth(i)
            except Exception:
                continue
    return None


def _count_visible_fields(form: Locator) -> int:
    loc = form.locator(SEL_FIELDS)
    n = 0
    for i in range(loc.count()):
        try:
            if loc.nth(i).is_visible():
                n += 1
        except Exception:
            continue
    return n


# --- Form filling -----------------------------------------------------------------
def _fill_form(p: Page, form: Locator, card: JobCard, cfg: dict,
               delays: list[float], deadline: float) -> str | None:
    """Fill everything we can answer; skip optional unknowns.

    Returns the label of the first REQUIRED field we couldn't answer, or None.
    """
    cover_text = cover_letter.generate(card, cfg)
    unanswered: str | None = None

    def note_unanswered(label: str) -> None:
        nonlocal unanswered
        if unanswered is None:
            unanswered = label or "(unlabelled field)"

    _upload_files(form, cfg, card, cover_text)

    # 1) text-ish inputs
    loc = form.locator(
        "input[type=text], input[type=email], input[type=tel], input[type=url], "
        "input[type=number], input[type=search], input:not([type])"
    )
    for i in range(loc.count()):
        if time.monotonic() > deadline:
            return unanswered
        el = loc.nth(i)
        try:
            if not el.is_visible() or el.input_value().strip():
                continue
            spec = _spec_for(el, "text")
            value = resolve_answer(spec, cfg)
            if value is None:
                if spec.required:
                    note_unanswered(spec.label)
                continue
            el.fill(str(value))
            _commit_combobox(p, el)
            human_delay([0.3, 0.9])
        except Exception as exc:
            log.debug("external text fill failed: %s", exc)

    # 2) textareas — cover-letter/motivation boxes get the tailored letter
    tloc = form.locator("textarea")
    for i in range(tloc.count()):
        if time.monotonic() > deadline:
            return unanswered
        el = tloc.nth(i)
        try:
            if not el.is_visible() or el.input_value().strip():
                continue
            spec = _spec_for(el, "textarea")
            if cover_text and _is_cover_letter_field(spec.label):
                el.fill(cover_text)
                continue
            value = resolve_answer(spec, cfg)
            if value is None:
                if spec.required:
                    note_unanswered(spec.label)
                continue
            el.fill(str(value))
            human_delay([0.3, 0.9])
        except Exception as exc:
            log.debug("external textarea fill failed: %s", exc)

    # 3) native <select> dropdowns
    sloc = form.locator("select")
    for i in range(sloc.count()):
        if time.monotonic() > deadline:
            return unanswered
        el = sloc.nth(i)
        try:
            if not el.is_visible():
                continue
            current = el.locator("option:checked")
            if current.count() and not _is_placeholder((current.first.inner_text() or "").strip() or "select"):
                continue  # something real is already selected
            spec = _spec_for(el, "select")
            spec.options = [o.strip() for o in el.locator("option").all_inner_texts()]
            choice = resolve_answer(spec, cfg)
            if choice is None:
                if spec.required:
                    note_unanswered(spec.label)
                continue
            el.select_option(label=choice)
            human_delay([0.3, 0.9])
        except Exception as exc:
            log.debug("external select fill failed: %s", exc)

    # 4) radio groups (grouped by name attribute)
    groups: dict[str, list[Locator]] = {}
    rloc = form.locator("input[type=radio]")
    for i in range(rloc.count()):
        el = rloc.nth(i)
        try:
            name = el.get_attribute("name") or f"_radio_{i}"
        except Exception:
            continue
        groups.setdefault(name, []).append(el)
    for name, els in groups.items():
        if time.monotonic() > deadline:
            return unanswered
        try:
            if any(_safe_is_checked(el) for el in els):
                continue
            opts = [_option_label(el) for el in els]
            glabel = _radio_group_label(els[0], name)
            required = any(_el_required(el) for el in els)
            spec = FieldSpec(label=glabel, kind="radio",
                             options=[o for o in opts if o], required=required)
            choice = resolve_answer(spec, cfg)
            if choice is None:
                if required:
                    note_unanswered(glabel)
                continue
            for el, opt in zip(els, opts):
                if opt and opt.strip().lower() == choice.strip().lower():
                    _safe_check(el)
                    break
            human_delay([0.3, 0.9])
        except Exception as exc:
            log.debug("external radio fill failed: %s", exc)

    # 5) checkboxes — tick required (consent) boxes only
    cloc = form.locator("input[type=checkbox]")
    for i in range(cloc.count()):
        if time.monotonic() > deadline:
            return unanswered
        el = cloc.nth(i)
        try:
            if _el_required(el) and not _safe_is_checked(el):
                _safe_check(el)
        except Exception as exc:
            log.debug("external checkbox failed: %s", exc)

    return unanswered


def _upload_files(form: Locator, cfg: dict, card: JobCard, cover_text: str | None) -> None:
    """Attach the CV (and optionally the cover-letter PDF). File inputs are
    usually hidden behind styled buttons; set_input_files works regardless."""
    files = form.locator("input[type=file]")
    if not files.count():
        return
    resume = resume_mod.resume_for_job(card, cfg)   # tailored per-JD CV, or static
    resume = str(Path(resume).resolve()) if resume else None
    cover_pdf = None
    if cover_text and cfg.get("cover_letter", {}).get("upload_pdf"):
        cover_pdf = cover_letter.render_pdf(cover_text, card.job_id, cfg)
    for i in range(files.count()):
        el = files.nth(i)
        label, _ = _label_for(el)
        label_l = label.lower()
        is_cover = any(w in label_l for w in ("cover", "letter", "personligt brev"))
        try:
            if is_cover:
                if cover_pdf:
                    el.set_input_files(cover_pdf)
            elif resume:
                el.set_input_files(resume)
                log.info("[external] attached CV to %r", label[:60] or "(file input)")
        except Exception as exc:
            log.debug("external file upload (%s) skipped: %s", label, exc)


# --- Per-element helpers ----------------------------------------------------------
def _spec_for(el: Locator, kind: str) -> FieldSpec:
    label, label_says_required = _label_for(el)
    itype = "text"
    if kind == "text":
        try:
            itype = (el.get_attribute("type") or "text").lower()
        except Exception:
            pass
    return FieldSpec(label=label, kind=kind, input_type=itype,
                     required=_el_required(el) or label_says_required)


def _label_for(el: Locator) -> tuple[str, bool]:
    """Resolve a field's label: label[for] -> aria-label -> placeholder ->
    closest label / aria-labelledby / fieldset legend -> name attribute.

    Second value: the raw label text itself marks the field required (*, 'required').
    """
    raw = _raw_label(el)
    says_required = "*" in raw or bool(re.search(r"\brequired\b", raw, re.I))
    return clean_label(raw), says_required


def _raw_label(el: Locator) -> str:
    try:
        fid = el.get_attribute("id")
        if fid and '"' not in fid:
            lab = el.page.locator(f'label[for="{fid}"]')
            if lab.count():
                t = (lab.first.inner_text() or "").strip()
                if t:
                    return t
    except Exception:
        pass
    for attr in ("aria-label", "placeholder"):
        try:
            v = el.get_attribute(attr)
            if v and v.strip():
                return v
        except Exception:
            pass
    try:
        t = el.evaluate(
            "el => {"
            "  const l = el.closest('label');"
            "  if (l && l.textContent.trim()) return l.textContent;"
            "  const ll = el.getAttribute('aria-labelledby');"
            "  if (ll) {"
            "    const t = ll.split(/\\s+/).map(id => {"
            "      const n = document.getElementById(id);"
            "      return n ? n.textContent : '';"
            "    }).join(' ');"
            "    if (t.trim()) return t;"
            "  }"
            "  const f = el.closest('fieldset');"
            "  const lg = f && f.querySelector('legend');"
            "  if (lg) return lg.textContent;"
            "  return '';"
            "}"
        )
        if t and t.strip():
            return t
    except Exception:
        pass
    try:
        name = el.get_attribute("name") or ""
        return re.sub(r"[\[\]_\-.]+", " ", name).strip()
    except Exception:
        return ""


def _option_label(el: Locator) -> str:
    """The text labelling one radio/checkbox option."""
    try:
        rid = el.get_attribute("id")
        if rid and '"' not in rid:
            lab = el.page.locator(f'label[for="{rid}"]')
            if lab.count():
                return (lab.first.inner_text() or "").strip()
    except Exception:
        pass
    try:
        return (el.evaluate("el => el.closest('label')?.textContent || ''") or "").strip()
    except Exception:
        return ""


def _radio_group_label(el: Locator, name: str) -> str:
    """The question text for a radio group: fieldset legend / [role=group] label."""
    try:
        t = el.evaluate(
            "el => {"
            "  const f = el.closest('fieldset');"
            "  const lg = f && f.querySelector('legend');"
            "  if (lg && lg.textContent.trim()) return lg.textContent;"
            "  const g = el.closest('[role=group],[role=radiogroup]');"
            "  if (g) return g.getAttribute('aria-label') || '';"
            "  return '';"
            "}"
        )
        if t and t.strip():
            return clean_label(t)
    except Exception:
        pass
    return clean_label(re.sub(r"[\[\]_\-.]+", " ", name).strip())


def _el_required(el: Locator) -> bool:
    try:
        if el.get_attribute("required") is not None:
            return True
        if (el.get_attribute("aria-required") or "").lower() == "true":
            return True
    except Exception:
        pass
    return False


def _safe_is_checked(el: Locator) -> bool:
    try:
        return el.is_checked()
    except Exception:
        return False


def _safe_check(el: Locator) -> None:
    """check() with the easy_apply._check_control fallback: hidden styled inputs
    are toggled by clicking their label instead."""
    try:
        el.check(timeout=3_000)
        return
    except Exception:
        pass
    try:
        rid = el.get_attribute("id")
        if rid and '"' not in rid:
            lab = el.page.locator(f'label[for="{rid}"]')
            if lab.count():
                lab.first.click()
                return
        el.evaluate("el => el.closest('label')?.click()")
    except Exception as exc:
        log.debug("external check fallback failed: %s", exc)


def _commit_combobox(p: Page, el: Locator) -> None:
    """If the input is an autocomplete/combobox (Greenhouse location, Ashby…),
    commit the first suggestion so the typed text becomes a valid selection."""
    try:
        role = (el.get_attribute("role") or "").lower()
        auto = (el.get_attribute("aria-autocomplete") or "").lower()
        if role != "combobox" and auto not in ("list", "both"):
            return
        p.wait_for_timeout(800)
        if p.locator("[role=option]").count():
            el.press("ArrowDown")
            el.press("Enter")
    except Exception as exc:
        log.debug("external combobox commit skipped: %s", exc)


# --- Submit & verify ----------------------------------------------------------------
_SUBMIT_NAME = re.compile(r"^\s*(submit|send|apply|skicka)", re.I)
_CONFIRM_TEXT = re.compile(
    r"thank you|thanks for applying|application (was |has been )?(received|submitted|sent)"
    r"|successfully (submitted|applied)|we('| ha)ve received your application"
    r"|tack för din ansökan|tack för att du",
    re.I,
)
_CONFIRM_URL = re.compile(r"thank|confirm|success|submitted", re.I)


def _submit(p: Page, form: Locator, card: JobCard, cfg: dict,
            deadline: float, ats: str) -> tuple[str, str]:
    btn = _submit_button(p, form)
    if btn is None:
        _screenshot(p, card, cfg, suffix="external-blocked")
        return ApplyResult.EXTERNAL, f"no submit button found ({ats})"
    pre_url = p.url
    btn.click()
    human_delay([2, 4])
    try:
        p.wait_for_load_state("domcontentloaded", timeout=15_000)
    except PWTimeout:
        pass
    for _ in range(6):
        if _confirmed(p, pre_url):
            _screenshot(p, card, cfg, suffix="external-submitted")
            return ApplyResult.APPLIED, f"external ({ats})"
        blocked = _blocking_gate(p, ats)
        if blocked:
            _screenshot(p, card, cfg, suffix="external-blocked")
            return ApplyResult.EXTERNAL, blocked
        if time.monotonic() > deadline:
            break
        p.wait_for_timeout(2_000)
    # Ambiguous: it may or may not have gone through — FAILED so the run retries.
    _screenshot(p, card, cfg, suffix="external-unverified")
    return ApplyResult.FAILED, f"external submission not confirmed ({ats})"


def _submit_button(p: Page, form: Locator) -> Locator | None:
    css = form.locator("button[type=submit], input[type=submit]")
    for i in range(css.count()):
        try:
            if css.nth(i).is_visible():
                return css.nth(i)
        except Exception:
            continue
    for scope in (form, p):
        btn = scope.get_by_role("button", name=_SUBMIT_NAME)
        for i in range(min(btn.count(), 5)):
            try:
                if btn.nth(i).is_visible():
                    return btn.nth(i)
            except Exception:
                continue
    return None


def _confirmed(p: Page, pre_url: str) -> bool:
    try:
        loc = p.get_by_text(_CONFIRM_TEXT)
        for i in range(min(loc.count(), 5)):
            if loc.nth(i).is_visible():
                return True
    except Exception:
        pass
    url = p.url
    return url != pre_url and bool(_CONFIRM_URL.search(url))


# --- Cleanup --------------------------------------------------------------------------
def _dismiss_ats_cookies(p: Page) -> None:
    """Best-effort cookie-consent dismissal on ATS pages (Teamtailor et al.)."""
    name = re.compile(
        r"^\s*(accept( all)?( cookies)?|allow all( cookies)?|agree|i agree|got it|ok(ay)?"
        r"|godkänn( alla)?|acceptera( alla)?|tillåt alla)\s*$",
        re.I,
    )
    try:
        btn = p.get_by_role("button", name=name)
        if btn.count() and btn.first.is_visible():
            btn.first.click()
            p.wait_for_timeout(500)
    except Exception as exc:
        log.debug("ATS cookie dismiss skipped: %s", exc)


def _restore_linkedin(page: Page, ats_page: Page | None) -> None:
    """Close the ATS tab (and any stray popups) and put the LinkedIn tab back in
    front, no matter how the attempt ended. `page` must stay usable."""
    try:
        if ats_page is not None and ats_page is not page and not ats_page.is_closed():
            ats_page.close()
    except Exception as exc:
        log.debug("ATS tab close failed: %s", exc)
    try:
        for extra in list(page.context.pages):
            if extra is not page and not extra.is_closed():
                extra.close()
    except Exception:
        pass
    try:
        page.bring_to_front()
    except Exception as exc:
        log.debug("bring_to_front failed: %s", exc)
