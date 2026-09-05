"""Entry point. Run:  python -m src.main   (from the project root)

Flags:
  --check           validate config.yaml + your files and print a summary; no browser
  --login           open the browser so you can sign in to LinkedIn once, then exit
  --config PATH     use a different config file (default: config.yaml)
  --live            override config and SUBMIT for real (disables dry_run)
  --max N           override max_applications_per_run
"""
from __future__ import annotations

import argparse
from pathlib import Path

from . import config as config_mod, llm_client
from .browser import PROFILE_DIR, Browser
from .linkedin import auth, easy_apply, external_apply, networking
from .linkedin.easy_apply import ApplyResult
from .linkedin.search import JobCard, iter_job_cards
from .tracker import Tracker
from .utils import acquire_run_lock, human_delay, load_dotenv, log, setup_logging


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Auto Job Applier Agent - applies to LinkedIn jobs for you. "
                    "First time: --check, then --login, then a dry run (no flags).")
    p.add_argument("--config", default="config.yaml", help="config file (default: config.yaml)")
    p.add_argument("--live", action="store_true", help="actually submit (disable dry_run)")
    p.add_argument("--max", type=int, default=None, help="override max applications this run")
    p.add_argument("--check", action="store_true",
                   help="validate config.yaml and your files, print a summary, and exit "
                        "(no browser, nothing is sent anywhere)")
    p.add_argument("--login", action="store_true",
                   help="just open the browser to log in and save the session, then exit")
    p.add_argument("--retry-skipped", action="store_true",
                   help="re-attempt previously skipped jobs (useful once new answers are configured)")
    p.add_argument("--retry-dry-run", action="store_true",
                   help="re-attempt jobs previewed during dry runs — use with --live to "
                        "actually submit the applications you reviewed as screenshots")
    p.add_argument("--recover", action="store_true",
                   help="URL-targeted recovery: re-attempt the jobs the bot reached but "
                        "couldn't finish (status external/failed), navigating straight to "
                        "each stored job URL. Skips deferred/manual and already-submitted ones.")
    return p.parse_args()


def main() -> None:
    load_dotenv()
    args = parse_args()
    cfg = config_mod.load_config(args.config)

    if args.live:
        cfg["safety"]["dry_run"] = False
    if args.max is not None:
        if args.max < 0:
            raise SystemExit("--max must be 0 or more.")
        cfg["safety"]["max_applications_per_run"] = args.max

    if args.check:
        raise SystemExit(_check(cfg))

    setup_logging(cfg["storage"].get("log_dir", "logs"))
    dry = cfg["safety"].get("dry_run", True)
    cap = cfg["safety"]["max_applications_per_run"]
    job_delays = cfg["safety"]["delay_between_jobs"]
    act_delays = cfg["safety"]["delay_between_actions"]
    timezone = (cfg.get("browser") or {}).get("timezone")

    # OS-level lock: two overlapping runs would double-apply (both pass
    # already_seen before either records). Held for the whole process lifetime.
    run_lock = acquire_run_lock()  # noqa: F841 — keep the handle alive
    if run_lock is None:
        raise SystemExit("Another run is already in progress — exiting.")

    if args.login:
        with Browser(headless=False, timezone=timezone) as br:
            auth.ensure_logged_in(br.page, cfg, act_delays)
        log.info("Session saved to the browser profile. Run the bot normally now.")
        return

    log.info("=" * 70)
    log.info("MODE: %s   |   cap this run: %d applications", "DRY RUN (no submit)" if dry else "LIVE SUBMIT", cap)
    log.info("=" * 70)

    tracker = Tracker(cfg["storage"]["database_path"], cfg,
                      retry_skipped=args.retry_skipped, retry_dry_run=args.retry_dry_run)

    daily_cap = cfg["safety"].get("max_applications_per_day")
    if daily_cap:
        done_today = tracker.count_today("applied") + tracker.count_today("dry_run")
        remaining = max(0, daily_cap - done_today)
        if remaining < cap:
            log.info("Daily cap %d: %d already done today — capping this run at %d.",
                     daily_cap, done_today, remaining)
            cap = remaining

    submitted = 0

    try:
        with Browser(headless=cfg["safety"].get("headless", False), timezone=timezone) as br:
            page = br.page
            auth.ensure_logged_in(page, cfg, act_delays)
            if args.recover:
                submitted = _run_recovery(page, cfg, tracker, cap, act_delays, job_delays)
            else:
                submitted = _run_search(page, cfg, tracker, cap, act_delays, job_delays)
    finally:
        _summary(tracker, submitted, dry)
        tracker.close()


def _attempt_apply(page, card, cfg, tracker, act_delays, job_delays) -> tuple[str, str]:
    """Apply to one job card. Honors role-dedup, retries a transient failure once,
    records the outcome (except LIMIT, handled by the caller).
    Returns (status, reason) — callers need the reason to tell a real submission
    from an "already applied" detection when counting against the caps."""
    # Aggregator reposts (same role, new job_id) — don't re-apply to a
    # title@company we already submitted to.
    dedupe = cfg["safety"].get("dedupe_by_role", True)
    if dedupe and tracker.already_applied_role(card.title, card.company):
        reason = f"duplicate role — already applied to {card.title} @ {card.company}"
        tracker.record(
            job_id=card.job_id, title=card.title, company=card.company,
            location=card.location, url=card.url, status="skipped", reason=reason,
        )
        return ApplyResult.SKIPPED, reason

    status, reason = ApplyResult.FAILED, "not attempted"
    for attempt in (1, 2):
        try:
            status, reason = easy_apply.open_and_apply(
                page, card, cfg, act_delays,
                dedupe_check=tracker.already_applied_role if dedupe else None)
            # No Easy Apply button -> try the company's own ATS
            # (apply_external never leaves `page` broken).
            if (status == ApplyResult.EXTERNAL
                    and cfg.get("external_apply", {}).get("enabled")):
                status, reason = external_apply.apply_external(page, card, cfg, act_delays)
        except Exception as exc:  # one bad job shouldn't kill the run
            status, reason = ApplyResult.FAILED, f"exception: {exc}"
            log.exception("Error applying to %s", card.job_id)
        if status != ApplyResult.FAILED or attempt > 1:
            break
        if "not confirmed" in reason:
            # The ATS submit may actually have gone through — retrying risks a
            # duplicate application.
            break
        log.info("Retrying %s once after failure: %s", card.job_id, reason)
        human_delay(job_delays)

    if status == ApplyResult.LIMIT:
        # Don't record — the job is retried tomorrow once LinkedIn's limit resets.
        log.warning("LinkedIn daily Easy Apply limit hit — ending the run.")
        return ApplyResult.LIMIT, reason

    tracker.record(
        job_id=card.job_id, title=card.title, company=card.company,
        location=card.location, url=card.url, status=status, reason=reason,
    )
    # Real submissions only — never network on dry runs or already-applied hits.
    if status == ApplyResult.APPLIED and not reason.startswith("already applied"):
        networking.connect_with_hiring_team(page, card, cfg, act_delays)
    return status, reason


def _counts_as_submission(status: str, reason: str) -> bool:
    """A new application this run — an 'already applied' detection is recorded
    for dedupe but must not consume the run/day budget."""
    return status in (ApplyResult.APPLIED, ApplyResult.DRY_RUN) \
        and not reason.startswith("already applied")


def _run_search(page, cfg, tracker, cap, act_delays, job_delays) -> int:
    submitted = 0
    # One attempt per job per run, even when searches/pagination re-surface it
    # (matters with --retry-skipped, which makes 'skipped' rows re-attemptable).
    processed: set[str] = set()
    # Regions are searched in order — earlier ones get the budget first.
    locations = cfg["search"].get("locations") or [cfg["search"].get("location", "")]
    for location, keyword in ((l, k) for l in locations for k in cfg["search"]["keywords"]):
        if submitted >= cap:
            break
        log.info("---- Searching: %r in %s ----", keyword, location)
        try:
            for card in iter_job_cards(page, keyword, cfg, act_delays, location=location):
                if submitted >= cap:
                    log.info("Hit per-run cap (%d). Stopping.", cap)
                    break
                if card.job_id in processed or tracker.already_seen(card.job_id):
                    continue
                processed.add(card.job_id)
                status, reason = _attempt_apply(page, card, cfg, tracker, act_delays, job_delays)
                if status == ApplyResult.LIMIT:
                    return submitted
                if _counts_as_submission(status, reason):
                    submitted += 1
                human_delay(job_delays)
        except Exception:  # a dead search page shouldn't kill the run either
            log.exception("Search for %r aborted; moving to the next keyword.", keyword)
            continue
    return submitted


def _run_recovery(page, cfg, tracker, cap, act_delays, job_delays) -> int:
    """URL-targeted recovery: re-attempt jobs the bot reached but couldn't finish,
    navigating straight to each stored LinkedIn job URL with the current code."""
    all_candidates = tracker.recoverable_jobs()
    # Bound LinkedIn page-visits per run (visiting hundreds of job pages in one
    # session looks like a bot). Least-recently attempted first — every attempt
    # refreshes the row's timestamp, so re-runs rotate through the backlog
    # instead of hammering the same failures.
    batch = int(cfg.get("safety", {}).get("recover_batch", 40))
    candidates = all_candidates[:batch]
    per_company = int(cfg.get("safety", {}).get("recover_per_company", 10))
    log.info("RECOVERY MODE: %d recoverable jobs total; processing the %d least-recently "
             "attempted this run (excludes deferred/manual and already-submitted; "
             "max %d per company).",
             len(all_candidates), len(candidates), per_company)
    submitted = 0
    company_count: dict[str, int] = {}
    for i, row in enumerate(candidates, 1):
        if submitted >= cap:
            log.info("Hit per-run cap (%d). Stopping.", cap)
            break
        company = (row.get("company") or "").strip().lower()
        if company and company_count.get(company, 0) >= per_company:
            log.info("[recover %d/%d] skipping %s — already applied to %d roles there this run.",
                     i, len(candidates), row.get("company"), per_company)
            continue
        card = JobCard(job_id=row["job_id"], title=row.get("title") or "",
                       company=row.get("company") or "", location=row.get("location") or "")
        log.info("[recover %d/%d] %s @ %s", i, len(candidates), card.title or card.job_id, card.company)
        status, reason = _attempt_apply(page, card, cfg, tracker, act_delays, job_delays)
        if status == ApplyResult.LIMIT:
            break
        if _counts_as_submission(status, reason):
            submitted += 1
            if company:
                company_count[company] = company_count.get(company, 0) + 1
        human_delay(job_delays)
    return submitted


def check_problems(cfg: dict) -> list[str]:
    """Things a person must fix before a real run: example values never
    replaced, missing files, a CV that isn't a PDF. Pure function, no I/O
    beyond reading the two personal files; nothing is sent anywhere."""
    a = cfg["applicant"]
    problems: list[str] = []
    first = str(a.get("first_name") or "").strip()
    last = str(a.get("last_name") or "").strip()
    if not first:
        problems.append("applicant.first_name is empty")
    if not last:
        problems.append("applicant.last_name is empty")
    # Only the exact example person is flagged - a real Jane or a real Doe is fine.
    if (first.lower(), last.lower()) == ("jane", "doe"):
        problems.append("applicant.first_name / last_name still hold the example person (Jane Doe)")
    email = str(a.get("email") or "").strip()
    if not email or email.endswith("@example.com") or "@" not in email:
        problems.append("applicant.email is empty, not an email address, or still the example")
    phone = str(a.get("phone") or "").strip()
    if not phone or phone == "5551234567":
        problems.append("applicant.phone is empty or still the example number")
    url = str(a.get("linkedin_url") or "").strip()
    if not url or "your-handle" in url or "linkedin.com/in/" not in url:
        problems.append("applicant.linkedin_url is empty or still the example handle")
    resume = Path(str(a.get("resume_path") or ""))
    try:
        head = resume.open("rb").read(5) if resume.is_file() else b""
    except OSError:
        head = b""
    if head != b"%PDF-":
        problems.append(f"{resume} is not a PDF file (it must start with %PDF)")
    profile = Path((cfg.get("cover_letter") or {}).get("profile_path", "data/profile.md"))
    if not profile.is_file():
        problems.append(f"{profile} is missing - copy profile.example.md there and fill it in")
    else:
        body = profile.read_text(encoding="utf-8", errors="replace")
        prose = "\n".join(l for l in body.splitlines() if l.strip() and not l.lstrip().startswith("#"))
        if "Jane Doe" in body:
            problems.append(f"{profile} still describes the example person (Jane Doe)")
        elif len(prose) < 80:
            problems.append(f"{profile} is nearly empty - describe your background under its headings")
    answers = cfg.get("answers") or {}
    wa = answers.get("work_authorization") or {}
    yes_no_keys = " ".join(str(k).lower() for k in (answers.get("yes_no") or {}))
    if not (wa.get("countries") or wa.get("regions")) and "authori" not in yes_no_keys:
        problems.append("answers.work_authorization.countries is empty - most forms ask "
                        "'are you authorised to work in ...?' and those jobs will be skipped")
    if not answers.get("languages"):
        problems.append("answers.languages is empty - language-level questions will be skipped "
                        "(add e.g. english: \"fluent\")")
    return problems


def _check(cfg: dict) -> int:
    """`--check`: the config already passed validation; print a plain-language
    summary of the setup and the problems to fix. Returns the exit status
    (1 when something must be fixed). No browser, no network, no secrets read."""
    a, s, safety = cfg["applicant"], cfg["search"], cfg["safety"]
    problems = check_problems(cfg)
    locations = s.get("locations") or [s.get("location", "")]
    dry = safety.get("dry_run", True)
    session = Path(PROFILE_DIR)
    has_session = session.is_dir() and any(session.iterdir())
    daily = safety.get("max_applications_per_day")
    # Plain ASCII on purpose: Windows consoles don't always render anything else.
    print("Config file:        OK")
    print(f"CV / resume:        {a.get('resume_path')}")
    print(f"Search keywords:    {', '.join(s['keywords'])}")
    print(f"Locations:          {', '.join(str(x) for x in locations)}")
    print(f"Title must contain: {', '.join(s.get('required_title_keywords') or []) or '(anything)'}")
    print(f"Mode:               {'DRY RUN - nothing is submitted' if dry else 'LIVE - applications WILL be submitted'}")
    print(f"Caps:               {safety['max_applications_per_run']} per run, "
          f"{daily if daily else 'no limit'} per day (dry runs count towards the day)")
    print(f"LLM:                {llm_client.describe_config(cfg)}")
    print(f"LinkedIn session:   {'saved (browser_profile/)' if has_session else 'not yet - run with --login'}")
    if problems:
        print("\nThings to fix before a real run:")
        for item in problems:
            print(f"  - {item}")
        return 1
    print("\nEverything looks filled in. Next: --login (once), then a dry run.")
    return 0


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
