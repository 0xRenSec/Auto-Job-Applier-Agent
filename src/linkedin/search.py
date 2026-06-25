"""Build LinkedIn job-search URLs and iterate over result cards."""
from __future__ import annotations

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
    if s.get("easy_apply_only", True):
        params["f_AL"] = "true"
    if start:
        params["start"] = str(start)
    return SEARCH_BASE + "?" + urllib.parse.urlencode(params)


def iter_job_cards(page: Page, keyword: str, cfg: dict, delays: list[float],
                   location: str | None = None):
    """Yield JobCard objects across paginated results for one keyword."""
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
            log.info("No more results for '%s' (start=%d).", keyword, start)
            return
        yield from batch

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


# Card lines that are UI chrome, not job data.
_CARD_NOISE = ("promoted", "easy apply", "viewed", "applied", "saved",
               "be an early applicant", "actively reviewing applicants",
               "company review time", "your profile matches")


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
        line = line.strip()
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
