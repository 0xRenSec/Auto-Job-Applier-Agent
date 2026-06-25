"""Entry point. Run:  python -m src.main   (from the project root)

Flags:
  --config PATH     use a different config file (default: config.yaml)
  --live            override config and SUBMIT for real (disables dry_run)
  --max N           override max_applications_per_run
"""
from __future__ import annotations

import argparse

from . import config as config_mod
from .browser import Browser
from .linkedin import auth, easy_apply, external_apply
from .linkedin.easy_apply import ApplyResult
from .linkedin.search import iter_job_cards
from .tracker import Tracker
from .utils import human_delay, load_dotenv, log, setup_logging


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="LinkedIn Easy Apply bot")
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--live", action="store_true", help="actually submit (disable dry_run)")
    p.add_argument("--max", type=int, default=None, help="override max applications this run")
    p.add_argument("--login", action="store_true",
                   help="just open the browser to log in and save the session, then exit")
    p.add_argument("--retry-skipped", action="store_true",
                   help="re-attempt previously skipped jobs (useful once new answers are configured)")
    return p.parse_args()


def main() -> None:
    load_dotenv()
    args = parse_args()
    cfg = config_mod.load_config(args.config)

    if args.live:
        cfg["safety"]["dry_run"] = False
    if args.max is not None:
        cfg["safety"]["max_applications_per_run"] = args.max

    setup_logging(cfg["storage"].get("log_dir", "logs"))
    dry = cfg["safety"].get("dry_run", True)
    cap = cfg["safety"]["max_applications_per_run"]
    job_delays = cfg["safety"]["delay_between_jobs"]
    act_delays = cfg["safety"]["delay_between_actions"]

    if args.login:
        with Browser(headless=False) as br:
            auth.ensure_logged_in(br.page, cfg, act_delays)
        log.info("Session saved to the browser profile. Run the bot normally now.")
        return

    log.info("=" * 70)
    log.info("MODE: %s   |   cap this run: %d applications", "DRY RUN (no submit)" if dry else "LIVE SUBMIT", cap)
    log.info("=" * 70)

    tracker = Tracker(cfg["storage"]["database_path"], cfg, retry_skipped=args.retry_skipped)

    daily_cap = cfg["safety"].get("max_applications_per_day")
    if daily_cap:
        done_today = tracker.count_today("applied") + tracker.count_today("dry_run")
        remaining = max(0, daily_cap - done_today)
        if remaining < cap:
            log.info("Daily cap %d: %d already done today — capping this run at %d.",
                     daily_cap, done_today, remaining)
            cap = remaining

    submitted = 0
    linkedin_limit = False

    try:
        with Browser(headless=cfg["safety"].get("headless", False)) as br:
            page = br.page
            auth.ensure_logged_in(page, cfg, act_delays)

            # Regions are searched in order — earlier ones get the budget first.
            locations = cfg["search"].get("locations") or [cfg["search"].get("location", "")]
            for location, keyword in ((l, k) for l in locations for k in cfg["search"]["keywords"]):
                if submitted >= cap or linkedin_limit:
                    break
                log.info("---- Searching: %r in %s ----", keyword, location)
                try:
                    for card in iter_job_cards(page, keyword, cfg, act_delays, location=location):
                        if submitted >= cap:
                            log.info("Hit per-run cap (%d). Stopping.", cap)
                            break
                        if tracker.already_seen(card.job_id):
                            continue
                        # Aggregator reposts (same role, new job_id) — don't
                        # re-apply to a title@company we already submitted to.
                        if cfg["safety"].get("dedupe_by_role", True) \
                                and tracker.already_applied_role(card.title, card.company):
                            tracker.record(
                                job_id=card.job_id, title=card.title, company=card.company,
                                location=card.location, url=card.url, status="skipped",
                                reason=f"duplicate role — already applied to {card.title} @ {card.company}",
                            )
                            continue

                        for attempt in (1, 2):
                            try:
                                status, reason = easy_apply.open_and_apply(page, card, cfg, act_delays)
                                # No Easy Apply button -> try the company's own
                                # ATS (apply_external never leaves `page` broken).
                                if (status == ApplyResult.EXTERNAL
                                        and cfg.get("external_apply", {}).get("enabled")):
                                    status, reason = external_apply.apply_external(
                                        page, card, cfg, act_delays)
                            except Exception as exc:  # one bad job shouldn't kill the run
                                status, reason = ApplyResult.FAILED, f"exception: {exc}"
                                log.exception("Error applying to %s", card.job_id)
                            if status != ApplyResult.FAILED or attempt > 1:
                                break
                            if "not confirmed" in reason:
                                # The ATS submit may actually have gone through —
                                # retrying risks a duplicate application.
                                break
                            # Failures are usually transient (timeouts, modal
                            # glitches) — one more try before recording.
                            log.info("Retrying %s once after failure: %s", card.job_id, reason)
                            human_delay(job_delays)

                        if status == ApplyResult.LIMIT:
                            # Don't record — the job stays unseen and is retried
                            # tomorrow, once LinkedIn's own limit resets.
                            log.warning("LinkedIn daily Easy Apply limit hit — ending the run.")
                            linkedin_limit = True
                            break

                        tracker.record(
                            job_id=card.job_id, title=card.title, company=card.company,
                            location=card.location, url=card.url, status=status, reason=reason,
                        )
                        if status in (ApplyResult.APPLIED, ApplyResult.DRY_RUN):
                            submitted += 1

                        human_delay(job_delays)
                except Exception:  # a dead search page shouldn't kill the run either
                    log.exception("Search for %r aborted; moving to the next keyword.", keyword)
                    continue
    finally:
        _summary(tracker, submitted, dry)
        tracker.close()


def _summary(tracker: Tracker, submitted: int, dry: bool) -> None:
    log.info("=" * 70)
    verb = "would-submit (dry run)" if dry else "submitted"
    log.info("Run finished. %d applications %s this run.", submitted, verb)
    log.info("Applied today (real): %d | dry-run today: %d",
             tracker.count_today("applied"), tracker.count_today("dry_run"))
    log.info("Review the DB at the configured database_path for the full log.")
    log.info("=" * 70)


if __name__ == "__main__":
    main()
