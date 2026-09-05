"""Best-effort connection request to the job's hiring team after applying.

After a successful (real) application, the job page often shows a "Meet the
hiring team" card. When networking.connect_with_hiring_team is enabled, we open
that member's profile and send a connection request without a note. Everything
here is best-effort: any missing UI element logs and returns — it must never
break or slow the application loop materially.
"""
from __future__ import annotations

import re

from ..utils import human_delay, log

_CONNECT_NAME = re.compile(r"^(connect$|invite .* to connect)", re.I)
_SEND_NAME = re.compile(r"send without a note|^send$", re.I)
_MORE_NAME = re.compile(r"^more", re.I)


def connect_with_hiring_team(page, card, cfg, delays) -> None:
    """Entry point — called from main after ApplyResult.APPLIED (never dry-run)."""
    if not cfg.get("networking", {}).get("connect_with_hiring_team"):
        return
    try:
        _connect(page, card, delays)
    except Exception as exc:
        # warning, not debug — a silent failure here means no connects happen
        # and nobody notices.
        log.warning("[network] connect attempt failed for %s: %s", card.job_id, exc)


def _hiring_team_profile(page) -> str | None:
    """Profile URL from the job page's hiring-team / job-poster card."""
    section = page.locator(
        "div.job-details-people-who-can-help__section, "
        "section:has-text('Meet the hiring team'), div.hirer-card__hirer-information"
    )
    if not section.count():
        return None
    link = section.first.locator("a[href*='/in/']")
    if not link.count():
        return None
    href = link.first.get_attribute("href") or ""
    if not href:
        return None
    return href if href.startswith("http") else f"https://www.linkedin.com{href}"


def _connect(page, card, delays) -> None:
    url = _hiring_team_profile(page)
    if not url:
        log.info("[network] no hiring-team member shown for %s", card.job_id)
        return
    page.goto(url, wait_until="domcontentloaded")
    human_delay(delays)

    # Scope to the profile TOP CARD: the page also renders Connect buttons in
    # "People also viewed" sidebar cards — page-wide matching either times out
    # or would invite the wrong person.
    top = page.locator("main section").first
    btn = top.get_by_role("button", name=_CONNECT_NAME)
    if not (btn.count() and btn.first.is_visible()):
        # Connect is often tucked under the top card's "More" menu.
        more = top.get_by_role("button", name=_MORE_NAME)
        if more.count() and more.first.is_visible():
            more.first.click(timeout=5_000)
            human_delay([0.5, 1.2])
            for role in ("menuitem", "button"):
                alt = page.get_by_role(role, name=re.compile(r"connect", re.I))
                if alt.count() and alt.first.is_visible():
                    btn = alt
                    break
    if not (btn.count() and btn.first.is_visible()):
        log.info("[network] no Connect option on hiring member's profile for %s "
                 "(already connected or follow-only).", card.job_id)
        return

    # Short timeout: an unclickable button should cost 5s, not 20.
    btn.first.click(timeout=5_000)
    human_delay([0.8, 1.6])
    send = page.get_by_role("button", name=_SEND_NAME)
    if send.count() and send.first.is_visible():
        send.first.click(timeout=5_000)
        log.info("[network] connection request sent to hiring-team member for %s @ %s",
                 card.title or card.job_id, card.company)
    else:
        log.info("[network] Connect dialog had no Send button for %s — left untouched.",
                 card.job_id)
