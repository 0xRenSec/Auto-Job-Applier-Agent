"""Playwright browser with a persistent profile (keeps you logged in)."""
from __future__ import annotations

import re
from pathlib import Path

from playwright.sync_api import BrowserContext, Page, sync_playwright

from .utils import log

PROFILE_DIR = "browser_profile"

# A normal, current desktop Chrome UA so we don't stand out.
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class Browser:
    def __init__(self, headless: bool):
        self.headless = headless
        self._pw = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None

    def __enter__(self) -> "Browser":
        Path(PROFILE_DIR).mkdir(exist_ok=True)
        self._pw = sync_playwright().start()
        # Persistent context => cookies/session survive between runs, so we log
        # in like a human once and reuse it. This is the single biggest thing
        # that keeps the account from looking like a bot.
        self.context = self._pw.chromium.launch_persistent_context(
            user_data_dir=PROFILE_DIR,
            headless=self.headless,
            user_agent=_UA,
            viewport={"width": 1440, "height": 900},
            locale="en-US",
            timezone_id="Europe/Stockholm",
            args=["--disable-blink-features=AutomationControlled"],
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
