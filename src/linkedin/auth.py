"""Ensure we have a logged-in LinkedIn session.

Strategy (most human-like first):
  1. Reuse the persistent browser profile. If already logged in, do nothing.
  2. Otherwise auto-login with credentials pulled from 1Password, entering a
     TOTP 2FA code if the prompt appears.
  3. If LinkedIn throws a captcha / device-verification we can't solve, pause and
     let the human finish in the visible window.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

from playwright.sync_api import Page, TimeoutError as PWTimeout

from .. import secrets
from ..utils import human_delay, log

FEED_URL = "https://www.linkedin.com/feed/"
LOGIN_URL = "https://www.linkedin.com/login"

# LinkedIn serves several login page variants; the IDs change but the
# name/autocomplete attributes are stable across them. :visible filters out
# the hidden duplicate inputs the new React login page renders.
SEL_LOGIN_EMAIL = (
    "#username:visible, input[name='session_key']:visible, "
    "input[type='email']:visible, input[autocomplete='username']:visible"
)
SEL_LOGIN_PASSWORD = (
    "#password:visible, input[name='session_password']:visible, "
    "input[type='password']:visible, input[autocomplete='current-password']:visible"
)


def _dismiss_cookie_banner(page: Page) -> None:
    """Clear LinkedIn's GDPR consent dialog so it can't intercept clicks.

    Prefers rejecting non-essential cookies; accepts as a last resort.
    """
    try:
        legacy = page.locator('button[action-type="DENY"]')
        if legacy.count() and legacy.first.is_visible():
            legacy.first.click()
            return
        for pattern in (r"^reject", r"^decline", r"^accept"):
            btn = page.get_by_role("button", name=re.compile(pattern, re.I))
            if btn.count() and btn.first.is_visible():
                btn.first.click()
                return
    except Exception as exc:
        log.debug("cookie banner dismissal: %s", exc)


def is_logged_in(page: Page) -> bool:
    page.goto(FEED_URL, wait_until="domcontentloaded")
    human_delay([2, 4])
    _dismiss_cookie_banner(page)
    # The global nav search box only renders for authenticated users. The feed
    # can be slow to hydrate, so wait for it rather than checking instantly.
    try:
        page.wait_for_selector("input.search-global-typeahead__input", timeout=10_000)
        return True
    except PWTimeout:
        pass
    return "/feed" in page.url and "login" not in page.url and "authwall" not in page.url


def ensure_logged_in(page: Page, cfg: dict, delays: list[float]) -> None:
    if is_logged_in(page):
        log.info("Existing LinkedIn session is valid — reusing it.")
        return

    log.info("No valid session. Attempting auto-login via 1Password credentials.")
    try:
        email, password = secrets.get_credentials(cfg)
    except secrets.SecretError as exc:
        log.warning("%s", exc)
        log.warning("Falling back to MANUAL login — sign in in the browser window (up to 10 min).")
        page.goto(LOGIN_URL, wait_until="domcontentloaded")
        if not _wait_for_manual_login(page, timeout_s=600):
            raise SystemExit("Login could not be completed. Re-run after logging in manually.")
        log.info("Logged in to LinkedIn.")
        return

    page.goto(LOGIN_URL, wait_until="domcontentloaded")
    human_delay(delays)
    _dismiss_cookie_banner(page)

    if "/feed" in page.url:
        log.info("Login page redirected to the feed — session is valid after all.")
        return

    # Full login form has an email box; the 'Welcome back' variant pre-fills
    # the account and only asks for a password. Fill whichever fields exist.
    email_box = page.locator(SEL_LOGIN_EMAIL).first
    pwd_box = page.locator(SEL_LOGIN_PASSWORD).first
    if email_box.count():
        _type(email_box, email)
        human_delay(delays)
    if pwd_box.count():
        _type(pwd_box, password)
        human_delay(delays)
        _debug_screenshot(page, "login-filled")
        _submit(page, pwd_box)
        human_delay([4, 6])
        log.info("Post-submit page: %s", page.url)
        _debug_screenshot(page, "login-after-submit")
        _handle_2fa(page, cfg, delays)
    else:
        log.warning("Login form not recognised — complete the login manually in the browser window.")

    # Give the human a chance to clear any captcha / "is this you?" challenge.
    if not is_logged_in(page):
        log.warning(
            "Auto-login did not land on the feed (captcha or device check likely). "
            "Complete the challenge in the browser window — waiting up to 10 min. "
            "Current page: %s", page.url,
        )
        _debug_screenshot(page)
        if not _wait_for_manual_login(page, timeout_s=600):
            raise SystemExit("Login could not be completed. Re-run after logging in manually.")

    log.info("Logged in to LinkedIn.")


def _type(field, value: str) -> None:
    """Type with real key events — the new login page ignores programmatic fill()."""
    field.click()
    field.press_sequentially(value, delay=60)


def _submit(page: Page, field) -> None:
    """Submit a login/2FA form whatever the button markup looks like."""
    btn = page.locator("button[type=submit]:visible")
    if btn.count():
        btn.first.click()
        return
    # New login page: the button isn't type=submit; match its accessible name
    # exactly so "Sign in with Apple" doesn't get clicked instead.
    btn = page.get_by_role("button", name=re.compile(r"^(sign in|submit)$", re.I))
    if btn.count():
        btn.first.click()
        return
    field.press("Enter")


def _wait_for_manual_login(page: Page, timeout_s: int) -> bool:
    """Poll until the human finishes logging in, on whatever page LinkedIn shows."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            if page.locator("input.search-global-typeahead__input").count() or (
                "/feed" in page.url and "login" not in page.url and "authwall" not in page.url
            ):
                return True
        except Exception:
            pass  # mid-navigation; just poll again
        page.wait_for_timeout(3000)
    return False


def _debug_screenshot(page: Page, name: str = "login-debug") -> None:
    """Snapshot the login flow at a named step so it can be diagnosed offline."""
    try:
        d = Path("data/screenshots")
        d.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(d / f"{name}.png"))
        log.info("Saved login screenshot data/screenshots/%s.png", name)
    except Exception as exc:
        log.debug("login debug screenshot failed: %s", exc)


def _handle_2fa(page: Page, cfg: dict, delays: list[float]) -> None:
    """If a 2FA PIN field is showing, fill it from the 1Password TOTP."""
    # .first: both selectors can match the same input, which would trip
    # Playwright's strict mode on fill()/wait_for().
    pin = page.locator("input#input__phone_verification_pin, input[name=pin]").first
    try:
        pin.wait_for(timeout=6000)
    except PWTimeout:
        return  # no 2FA prompt

    code = secrets.get_otp(cfg)
    if not code:
        log.warning("2FA prompt detected but no TOTP in 1Password. Enter the code manually.")
        page.wait_for_timeout(120_000)
        return

    log.info("Entering 2FA code from 1Password TOTP.")
    pin.fill(code)
    human_delay(delays)
    _submit(page, pin)
    human_delay([3, 5])
