#!/usr/bin/env python3
"""Gemini Usage Tracker: auth -> fetch -> transform -> write -> dashboard.

    python run.py                    # full run using config.yaml
    python run.py --dry-run          # authenticate and count records, write nothing
    python run.py --smoke-test       # last 24 hours: record count + first record's parameters
    python run.py --from-cache output/raw_2026-09-25.json   # re-run transform offline
    python run.py --compare output/org_summary_2026-08-28.csv
"""
from __future__ import annotations

import argparse
import glob
import logging
import sys
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from src import fetch, report, transform
from src.config import MAX_RECOMMENDED_WINDOW_DAYS, ConfigError, load_config

BASE = Path(__file__).resolve().parent
log = logging.getLogger("gemini_usage")

ZERO_RECORDS_HELP = """
No Gemini records were returned. The three most likely causes:
  1. No Gemini license: only users with a Gemini-capable Workspace edition or
     add-on (or Workspace Labs) produce these events.
  2. No usage in the window: nobody used Gemini in this period, or events have
     not arrived yet (audit data can lag by a few hours).
  3. Wrong scope / account: the signed-in account is not a super admin, or the
     token was granted without the audit.readonly scope. Delete token.json and
     run again to sign in fresh.
"""


def _creds(cfg, open_browser=True):
    from src.auth import get_credentials
    return get_credentials(cfg, open_browser=open_browser)


def smoke_test(cfg: dict, open_browser: bool = True) -> int:
    """One API call for the last 24 hours. Returns the record count."""
    creds = _creds(cfg, open_browser)
    reports, directory = fetch.build_services(creds)
    start, end = fetch.window(1)
    print(f"\nFetching Gemini events from {start} to {end} (first page only)...")
    items = fetch.fetch_activities(reports, start, end, cfg["customer_id"], max_pages=1)
    more = " (more pages available)" if len(items) >= 1000 else ""
    print(f"\nRecords returned: {len(items)}{more}")
    if items:
        first = items[0]
        print(f"First record: user={first.get('actor', {}).get('email')} time={first.get('id', {}).get('time')}")
        for ev in first.get("events", []):
            names = [p.get("name") for p in ev.get("parameters", [])]
            print(f"  event type={ev.get('type')} name={ev.get('name')}")
            print(f"  parameter names: {names}")
        rep = transform.parameter_report(items, cfg["param_map"])
        print(f"Event names in this page: {rep['event_names']}")
        print(f"App values in this page:  {rep['raw_app_values']}")
        if rep["unknown_parameters"]:
            print(f"Unrecognised parameter names (kept in raw export): {rep['unknown_parameters']}")
    else:
        print(ZERO_RECORDS_HELP)
    users = fetch.fetch_users(directory, cfg["customer_id"], progress=None)
    print(f"Directory users visible: {len(users)}")
    return len(items)


def latest_cache() -> Path | None:
    files = sorted(glob.glob(str(BASE / "output" / "raw_*.json")))
    return Path(files[-1]) if files else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Gemini usage report for Google Workspace")
    ap.add_argument("--config", default=str(BASE / "config.yaml"))
    ap.add_argument("--days", type=int, help="override window_days from config.yaml")
    ap.add_argument("--dry-run", action="store_true", help="authenticate and count records; write nothing")
    ap.add_argument("--smoke-test", action="store_true", help="last 24h: count + parameter names")
    ap.add_argument("--from-cache", nargs="?", const="latest", metavar="RAW_JSON",
                    help="skip the API and re-use a cached output/raw_*.json (default: newest)")
    ap.add_argument("--compare", metavar="ORG_SUMMARY_CSV", help="previous org_summary CSV to compare against")
    ap.add_argument("--no-browser", action="store_true", help="do not open a browser (sign-in URL is printed)")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s: %(message)s")
    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        print(f"Config error: {e}", file=sys.stderr)
        return 2
    if args.days:
        cfg["window_days"] = args.days
    if cfg["window_days"] > MAX_RECOMMENDED_WINDOW_DAYS:
        log.warning("window_days=%s is longer than %s days. Audit-log retention is limited, "
                    "so older events may be missing and the numbers will look low.",
                    cfg["window_days"], MAX_RECOMMENDED_WINDOW_DAYS)

    if args.smoke_test:
        smoke_test(cfg, open_browser=not args.no_browser)
        return 0

    out_dir = BASE / "output"
    run_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    if args.from_cache:
        cache_path = latest_cache() if args.from_cache == "latest" else Path(args.from_cache)
        if not cache_path or not cache_path.exists():
            print("No cached raw_*.json found in output/. Run without --from-cache first.", file=sys.stderr)
            return 2
        print(f"Using cached API response {cache_path}")
        cache = fetch.load_cache(cache_path)
        activities, users = cache["activities"], cache["users"]
        start, end = cache["start_time"], cache["end_time"]
        cfg["window_days"] = round((pd.Timestamp(end) - pd.Timestamp(start)).total_seconds() / 86400)
    else:
        creds = _creds(cfg, open_browser=not args.no_browser)
        reports, directory = fetch.build_services(creds)
        start, end = fetch.window(cfg["window_days"])
        print(f"Fetching Gemini events {start} -> {end} ...")
        activities = fetch.fetch_activities(reports, start, end, cfg["customer_id"])
        print("Fetching user directory ...")
        users = fetch.fetch_users(directory, cfg["customer_id"])
        if args.dry_run:
            n_events = sum(len(a.get("events") or []) for a in activities)
            print(f"\nDry run: {len(activities)} activity records ({n_events} events), "
                  f"{len(users)} directory users. No files written.")
            if not activities:
                print(ZERO_RECORDS_HELP)
            return 0
        cache_path = out_dir / f"raw_{run_date}.json"
        fetch.save_cache(cache_path, activities, users, start, end)
        print(f"Cached API response to {cache_path}")

    if not activities:
        print(ZERO_RECORDS_HELP)

    rep = transform.parameter_report(activities, cfg["param_map"])
    transform.log_parameter_report(rep, cfg["usage_event_names"])

    events = transform.flatten(activities, cfg["param_map"])
    summary = transform.build_user_summary(
        events, users,
        usage_event_names=cfg["usage_event_names"],
        include_suspended=cfg["include_suspended"],
        ou_filter=cfg["ou_filter"],
        tz=cfg["timezone"],
    )
    scoped = transform.scoped_events(events, summary, cfg["usage_event_names"])
    org = transform.build_org_summary(summary, scoped, start_time=start, end_time=end,
                                      window_days=cfg["window_days"])

    comparison = None
    if args.compare:
        comparison = transform.compare_org_summaries(pd.read_csv(args.compare), org)

    paths = report.write_csvs(out_dir, run_date, events, summary, org)
    ctx = report.dashboard_context(summary, scoped, org, comparison)
    dash = report.render_dashboard(out_dir, run_date, ctx)

    m = dict(zip(org["metric"], org["value"]))
    print(f"\nUsers in scope: {m['total_users']}  Active: {m['active_users']}  "
          f"Adoption: {m['adoption_pct']}%  Actions: {m['total_actions']}")
    for p in list(paths.values()) + [dash]:
        print(f"  wrote {p}")
    if not args.no_browser:
        webbrowser.open(dash.resolve().as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())
