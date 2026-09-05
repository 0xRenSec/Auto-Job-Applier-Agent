"""Build LinkedIn job-search URLs and iterate over result cards."""
from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass

from playwright.sync_api import Page

from ..utils import human_delay, log

# LinkedIn search filter codes.
_WORKPLACE = {"on_site": "1", "remote": "2", "hybrid": "3"}
_DATE = {"past_24h": "r86400", "past_week": "r604800", "past_month": "r2592000", "any": ""}
_EXPERIENCE = {
    "internship": "1", "entry": "2", "associate": "3",
    "mid_senior": "4", "director": "5", "executive": "6",
}
_JOB_TYPE = {
    "full_time": "F", "part_time": "P", "contract": "C",
    "temporary": "T", "volunteer": "V", "internship": "I", "other": "O",
}

SEARCH_BASE = "https://www.linkedin.com/jobs/search/"


@dataclass
class JobCard:
    job_id: str
    title: str
    company: str
    location: str
    description: str = ""

    @property
    def url(self) -> str:
        return f"https://www.linkedin.com/jobs/view/{self.job_id}/"


def build_url(keyword: str, cfg: dict, start: int = 0, location: str | None = None) -> str:
    s = cfg["search"]
    params: dict[str, str] = {
        "keywords": keyword,
        "location": location if location is not None else s.get("location", ""),
    }
    wt = [_WORKPLACE[w] for w in s.get("workplace_types", []) if w in _WORKPLACE]
    if wt:
        params["f_WT"] = ",".join(wt)
    if _DATE.get(s.get("date_posted", "any")):
        params["f_TPR"] = _DATE[s["date_posted"]]
    exp = [_EXPERIENCE[e] for e in s.get("experience_levels", []) if e in _EXPERIENCE]
    if exp:
        params["f_E"] = ",".join(exp)
    jt = [_JOB_TYPE[j] for j in s.get("job_types", []) if j in _JOB_TYPE]
    if jt:
        params["f_JT"] = ",".join(jt)
    if s.get("easy_apply_only", True):
        params["f_AL"] = "true"
    if start:
        params["start"] = str(start)
    return SEARCH_BASE + "?" + urllib.parse.urlencode(params)


def iter_job_cards(page: Page, keyword: str, cfg: dict, delays: list[float],
                   location: str | None = None):
    """Yield JobCard objects across paginated results for one keyword.

    Stops as soon as a results page contains no job we haven't already yielded
    for this keyword — deep pagination otherwise serves the same promoted
    cards forever and the keyword never terminates.
    """
    yielded: set[str] = set()
    start = 0
    while True:
        url = build_url(keyword, cfg, start=start, location=location)
        try:
            page.goto(url, wait_until="domcontentloaded")
        except Exception as exc:
            # Transient network blip — wait, retry once, then give up on this
            # keyword instead of killing the whole run.
            log.warning("Search page load failed (%s); retrying once.", exc)
            human_delay([8, 15])
            try:
                page.goto(url, wait_until="domcontentloaded")
            except Exception:
                log.warning("Search still failing — ending keyword %r.", keyword)
                return
        human_delay(delays)
        from .auth import _dismiss_cookie_banner
        _dismiss_cookie_banner(page)
        _scroll_results(page, delays)

        # Snapshot the whole page of cards BEFORE yielding any: the caller
        # navigates away to apply, which invalidates live locators on this page.
        batch = _collect_cards(page)
        if not batch:
            batch = _collect_cards_new_ui(page, delays)
        fresh = [c for c in batch if c.job_id not in yielded]
        if not fresh:
            log.info("No new results for '%s' (start=%d) — ending keyword.", keyword, start)
            return
        yielded.update(c.job_id for c in fresh)
        yield from fresh

        # LinkedIn pages results 25 at a time.
        start += 25
        if start > 975:  # LinkedIn caps search depth ~1000 results.
            return


def _collect_cards(page: Page) -> list[JobCard]:
    cards = page.locator("div.job-card-container, li.jobs-search-results__list-item")
    out: list[JobCard] = []
    seen: set[str] = set()
    for i in range(cards.count()):
        card = cards.nth(i)
        job_id = card.get_attribute("data-job-id")
        if not job_id:
            inner = card.locator("[data-job-id]")
            if inner.count():
                job_id = inner.first.get_attribute("data-job-id")
        if not job_id:
            link = card.locator("a.job-card-container__link, a.job-card-list__title")
            href = link.first.get_attribute("href") if link.count() else None
            job_id = _job_id_from_href(href)
        if not job_id or job_id in seen:
            continue
        seen.add(job_id)
        title = _safe_text(card, "a.job-card-container__link, a.job-card-list__title, a[href*='/jobs/view/']")
        company = _safe_text(
            card,
            ".job-card-container__primary-description, .job-card-container__company-name, "
            ".artdeco-entity-lockup__subtitle",
        )
        location = _safe_text(
            card,
            ".job-card-container__metadata-item, .artdeco-entity-lockup__caption",
        )
        if not (title and company and location):
            t2, c2, l2 = _parse_card_lines(card)
            title, company, location = title or t2, company or c2, location or l2
        out.append(JobCard(job_id=job_id, title=title, company=company, location=location))
    return out


# The 2026 search UI (/jobs/search-results) strips every stable hook: no
# div.job-card-container, no [data-job-id], obfuscated one-off class names, and
# the job id appears NOWHERE in the card's DOM. The only place it surfaces is
# the ?currentJobId= URL param after the card is clicked (SPA selection). So:
# tag the card elements with a temporary attribute, click each one, and read
# the id off the URL. The results list is found structurally — the div under
# <main> with the most element children that contains company-logo images.
_TAG_CARDS_JS = """
() => {
  const lists = Array.from(document.querySelectorAll('main div'))
    .filter(el => el.children.length >= 4 && el.querySelector('img'));
  const list = lists.sort((a, b) => b.children.length - a.children.length)[0];
  if (!list) return 0;
  const cards = Array.from(list.children)
    .filter(k => ((k.innerText || '').trim().length > 20));
  cards.forEach((c, i) => c.setAttribute('data-lijaa-card', String(i)));
  return cards.length;
}
"""

_CURRENT_JOB_ID = re.compile(r"[?&]currentJobId=(\d+)")


def _collect_cards_new_ui(page: Page, delays: list[float]) -> list[JobCard]:
    """Collect cards on the new search UI by clicking each card and reading
    ?currentJobId= from the URL. Title/company/location come from the card's
    text lines (same fallback parser as the old UI). Returns [] when the page
    has no recognisable results list, so callers treat it as an empty page."""
    try:
        n = page.evaluate(_TAG_CARDS_JS)
    except Exception:
        return []
    out: list[JobCard] = []
    seen: set[str] = set()
    prev_id = None
    for i in range(int(n or 0)):
        card = page.locator(f"[data-lijaa-card='{i}']")
        if not card.count():
            continue
        try:
            title, company, location = _parse_card_lines(card.first)
            if not title:
                continue
            card.first.scroll_into_view_if_needed(timeout=3000)
            card.first.click(timeout=3000)
        except Exception:
            continue
        human_delay([0.4, 0.9])
        job_id = _current_job_id(page, changed_from=prev_id)
        # A click that doesn't move currentJobId off the previous card usually
        # means it didn't register — one JS-dispatch retry, then give up on it.
        # (First card is exempt: the page auto-selects it on load, so the URL
        # already carries its id before any click.)
        if job_id is None and i > 0:
            try:
                card.first.evaluate(
                    "el => el.dispatchEvent(new MouseEvent('click', "
                    "{bubbles: true, cancelable: true, view: window}))")
                human_delay([0.6, 1.2])
                job_id = _current_job_id(page, changed_from=prev_id)
            except Exception:
                pass
        if job_id is None and i == 0:
            m = _CURRENT_JOB_ID.search(page.url)
            job_id = m.group(1) if m else None
        if not job_id or job_id in seen:
            continue
        seen.add(job_id)
        prev_id = job_id
        out.append(JobCard(job_id=job_id, title=title, company=company,
                           location=location))
    if out:
        log.info("New-UI search page: harvested %d cards via click-selection.",
                 len(out))
    return out


def _current_job_id(page: Page, changed_from: str | None) -> str | None:
    """currentJobId from the SPA URL, or None if it still equals `changed_from`
    (the click didn't select a new card). Polls briefly — the SPA updates the
    URL a beat after the click."""
    for _ in range(6):
        m = _CURRENT_JOB_ID.search(page.url)
        if m and m.group(1) != changed_from:
            return m.group(1)
        page.wait_for_timeout(400)
    return None


# Card lines that are UI chrome, not job data.
_CARD_NOISE = ("promoted", "easy apply", "viewed", "applied", "saved",
               "be an early applicant", "actively reviewing applicants",
               "company review time", "your profile matches",
               # 2026 UI additions
               "you’d be a top applicant", "you'd be a top applicant",
               "posted ", "promoted by hirer", "how promoted jobs",
               "school alumni work", "company alumni work")

# Badge text the 2026 UI splices into the card's a11y title line. Stripping it
# makes that line identical to the visible title line that follows, so the
# dedupe in _parse_card_lines collapses them instead of shifting every field
# by one (title landing in company, company in location, ...).
_BADGE_RES = (
    re.compile(r"^selected,\s*", re.I),
    re.compile(r"\s*\(verified job\)$", re.I),
    re.compile(r"\s+with verification$", re.I),
)


def _clean_card_line(line: str) -> str:
    for rx in _BADGE_RES:
        line = rx.sub("", line)
    return line.strip()


def _parse_card_lines(card) -> tuple[str, str, str]:
    """Fallback for the new UI's obfuscated classes: the card's visible text is
    title (often duplicated for a11y), company, location, then metadata."""
    try:
        raw = (card.inner_text() or "").splitlines()
    except Exception:
        return "", "", ""
    lines: list[str] = []
    seen: set[str] = set()
    for line in raw:
        line = _clean_card_line(line.strip())
        key = line.lower()
        if not line or key in seen or any(key.startswith(n) for n in _CARD_NOISE):
            continue
        seen.add(key)
        lines.append(line)
    lines += ["", "", ""]
    return lines[0], lines[1], lines[2]


def _scroll_results(page: Page, delays: list[float]) -> None:
    """Lazy-loaded list — scroll the results column so all cards render."""
    try:
        for _ in range(6):
            page.mouse.wheel(0, 1500)
            human_delay([0.6, 1.4])
    except Exception:
        pass


def _safe_text(card, selector: str) -> str:
    loc = card.locator(selector)
    if loc.count() == 0:
        return ""
    # Cards repeat the text in a visually-hidden span — keep the first line only.
    return (loc.first.inner_text() or "").strip().split("\n")[0]


def _job_id_from_href(href: str | None) -> str | None:
    if not href:
        return None
    # /jobs/view/1234567890/
    for part in href.split("/"):
        if part.isdigit() and len(part) >= 6:
            return part
    return None
