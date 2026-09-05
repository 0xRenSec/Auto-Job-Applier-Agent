"""Unit tests for visible-modal resolution — offline Chromium, no LinkedIn.

Job pages that embed a video carry hidden Video.js `div[role="dialog"]`
decoys. They sort BEFORE the real Easy Apply modal in the DOM, so any
`.first` / `wait_for_selector` on SEL_MODAL resolves to a permanently
hidden element. That silently failed a batch of applications before it was
diagnosed.

Run:  python -m pytest tests/test_modal_locator.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import TimeoutError as PWTimeout, sync_playwright  # noqa: E402

from src.linkedin.easy_apply import (SEL_MODAL, dismiss_safety_interstitial,  # noqa: E402
                                     visible_modal)

# The real page structure, reduced: two hidden Video.js dialogs emitted by the
# embedded player, then LinkedIn's actual modal.
FIXTURE = """
<html><body>
  <div class="vjs-error-display vjs-modal-dialog vjs-hidden" role="dialog"
       aria-hidden="true" style="display:none">This is a modal window.</div>
  <div class="vjs-modal-dialog vjs-hidden vjs-text-track-settings" role="dialog"
       aria-hidden="true" style="display:none">Captions settings</div>
  <div class="artdeco-modal jobs-easy-apply-modal" role="dialog">
    <div class="artdeco-modal__content">Apply to Acme</div>
    <button aria-label="Continue to next step">Next</button>
  </div>
</body></html>
"""

# Same page before the Easy Apply modal has rendered: decoys only.
FIXTURE_NO_MODAL = """
<html><body>
  <div class="vjs-error-display vjs-modal-dialog vjs-hidden" role="dialog"
       aria-hidden="true" style="display:none">This is a modal window.</div>
</body></html>
"""


def _page(pw, html):
    page = pw.chromium.launch(headless=True).new_page()
    page.set_content(html)
    return page


def test_raw_selector_resolves_to_the_hidden_decoy():
    """Documents the trap: SEL_MODAL.first is NOT the real modal."""
    with sync_playwright() as pw:
        page = _page(pw, FIXTURE)
        assert page.locator(SEL_MODAL).count() == 3
        assert page.locator(SEL_MODAL).first.is_visible() is False


def test_raw_wait_for_selector_times_out_despite_open_modal():
    """The actual reported bug: 'Easy Apply modal did not open'."""
    with sync_playwright() as pw:
        page = _page(pw, FIXTURE)
        try:
            page.wait_for_selector(SEL_MODAL, timeout=1_500)
            raise AssertionError("expected a timeout on the hidden decoy")
        except PWTimeout:
            pass


def test_visible_modal_resolves_to_the_real_modal():
    with sync_playwright() as pw:
        page = _page(pw, FIXTURE)
        modal = visible_modal(page)
        assert modal.count() == 1
        assert modal.is_visible() is True
        assert "jobs-easy-apply-modal" in (modal.get_attribute("class") or "")


def test_visible_modal_scopes_button_lookup_correctly():
    """Line 394's failure mode: buttons must be found inside the REAL modal."""
    with sync_playwright() as pw:
        page = _page(pw, FIXTURE)
        btn = visible_modal(page).get_by_role("button", name="Next")
        assert btn.count() == 1


def test_visible_modal_is_empty_when_only_decoys_present():
    """A page with decoys but no real modal must still read as 'no modal'."""
    with sync_playwright() as pw:
        page = _page(pw, FIXTURE_NO_MODAL)
        assert page.locator(SEL_MODAL).count() == 1     # decoy is there...
        assert visible_modal(page).count() == 0         # ...but no real modal


# --- LinkedIn's "Job search safety reminder" interstitial --------------------
# A native <dialog> with NO role attribute and hashed class names, interposed
# between the Easy Apply click and the modal. SEL_MODAL cannot match it, so the
# bot reported "modal did not open" for every such posting. Its buttons are
# not exposed as role=button either, so match on text.

FIXTURE_INTERSTITIAL = """
<html><body>
  <dialog open>
    <h2>Job search safety reminder</h2>
    <button>Review job post</button>
    <button onclick="document.title='continued'">Continue applying</button>
  </dialog>
</body></html>
"""


def test_interstitial_is_invisible_to_the_modal_selector():
    """Documents why it surfaced as 'modal did not open'."""
    with sync_playwright() as pw:
        page = _page(pw, FIXTURE_INTERSTITIAL)
        assert page.locator(SEL_MODAL).count() == 0


def test_dismiss_safety_interstitial_clicks_continue_applying():
    with sync_playwright() as pw:
        page = _page(pw, FIXTURE_INTERSTITIAL)
        assert dismiss_safety_interstitial(page) is True
        assert page.title() == "continued"      # the RIGHT button was clicked


def test_dismiss_safety_interstitial_is_a_noop_without_one():
    with sync_playwright() as pw:
        page = _page(pw, FIXTURE)               # the normal open-modal page
        assert dismiss_safety_interstitial(page) is False

if __name__ == "__main__":
    import traceback
    failed = 0
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    for fn in tests:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL  {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} tests passed")
    sys.exit(1 if failed else 0)
