"""Playwright browser with a persistent profile (keeps you logged in)."""
from __future__ import annotations

import re
from pathlib import Path

from playwright.sync_api import BrowserContext, Page, sync_playwright

from .utils import log

PROFILE_DIR = "browser_profile"

# No user-agent override (2026-08-12). The old spoof ("Mac, Chrome 131") had
# gone stale — a 2024 browser claim over a current Chromium, contradicted by
# client hints — and LinkedIn started serving the mobile-style search shell.
# The engine's own UA is current and self-consistent, which stands out less.


class Browser:
    def __init__(self, headless: bool, timezone: str | None = None, locale: str = "en-US"):
        self.headless = headless
        # config.yaml -> browser.timezone (e.g. "Europe/Berlin"). Blank = the
        # machine's own timezone, which is what a real user's browser reports.
        self.timezone = (timezone or "").strip() or None
        self.locale = locale or "en-US"
        self._pw = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None

    def __enter__(self) -> "Browser":
        Path(PROFILE_DIR).mkdir(exist_ok=True)
        self._pw = sync_playwright().start()
        try:
            # Persistent context => cookies/session survive between runs, so we log
            # in like a human once and reuse it. This is the single biggest thing
            # that keeps the account from looking like a bot.
            extra = {"timezone_id": self.timezone} if self.timezone else {}
            self.context = self._pw.chromium.launch_persistent_context(
                user_data_dir=PROFILE_DIR,
                headless=self.headless,
                viewport={"width": 1440, "height": 900},
                locale=self.locale,
                args=["--disable-blink-features=AutomationControlled"],
                **extra,
            )
            # Hide the navigator.webdriver flag that screams "automation".
            self.context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            )
            self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
            self.page.set_default_timeout(20_000)
            # Whenever LinkedIn's cookie-consent banner appears (it can pop up at
            # any point in a session) and blocks an action, auto-click "Reject"
            # (declines non-essential cookies only).
            self.page.add_locator_handler(
                self.page.get_by_role("button", name=re.compile(r"^reject$", re.I)),
                lambda btn: btn.click(),
            )
        except Exception:
            # __exit__ never runs if __enter__ raises — clean up here or the
            # driver/Chromium keep running and hold the profile lock.
            self.__exit__()
            raise
        log.info("Browser launched (headless=%s).", self.headless)
        return self

    def __exit__(self, *exc) -> None:
        try:
            if self.context:
                self.context.close()
        finally:
            if self._pw:
                self._pw.stop()
        log.info("Browser closed.")
