#!/usr/bin/env python3
"""Build a demo CIO dashboard for a fictional company, safe to host publicly.

Everything is invented: people, names, emails, org units and every number. No tenant
data, credentials or cache files are read. The output is a single static page:

    python tools/make_demo_dashboard.py                 # -> demo_site/index.html
    python tools/make_demo_dashboard.py --users 400 --days 120 --seed 7

Deploy demo_site/ to any static host, e.g. `npx vercel deploy demo_site --prod`.
"""
from __future__ import annotations

import argparse
import math
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from src import cio, transform  # noqa: E402
from src.config import DEFAULTS  # noqa: E402

FIRST = ["Aarav", "Aisha", "Alex", "Amelia", "Ana", "Arjun", "Ben", "Carlos", "Chen", "Chloe", "Daniel", "Diya",
         "Elena", "Emma", "Ethan", "Fatima", "Felix", "Grace", "Hana", "Hiro", "Isabel", "Ivan", "Jack", "Jia",
         "Julia", "Kai", "Kavya", "Leo", "Lina", "Lucas", "Maya", "Mei", "Mohammed", "Nadia", "Noah", "Nora",
         "Oliver", "Omar", "Priya", "Rafael", "Ravi", "Rin", "Rohan", "Sara", "Sofia", "Tariq", "Thomas", "Uma",
         "Victor", "Wei", "Yara", "Yusuf", "Zara", "Zoe", "Hannah", "Marcus", "Ines", "Kenji", "Lara", "Samir"]
LAST = ["Anderson", "Bakker", "Chen", "Costa", "Das", "Dubois", "Evans", "Fernandes", "Garcia", "Gupta", "Haddad",
        "Ito", "Jensen", "Kapoor", "Kim", "Kowalski", "Lee", "Lim", "Martin", "Mehta", "Mendes", "Moreau", "Nair",
        "Nguyen", "Novak", "Okafor", "Olsen", "Park", "Patel", "Petrov", "Quinn", "Rahman", "Reyes", "Rossi",
        "Sato", "Schmidt", "Shah", "Sharma", "Silva", "Singh", "Tan", "Taylor", "Tran", "Vargas", "Wang", "Weber",
        "Wong", "Yamamoto", "Yilmaz", "Zhang"]

# Only apps that appeared in a real tenant's Gemini audit log during testing are used
# (gmail, docs, sheets, drive, meet, calendar, workflows, gemini_app), so the demo never
# shows a breakdown the real tool could not produce. Feature / action values come from
# Google's documented lists for this log.
TESTED_APPS = {"gmail", "docs", "sheets", "drive", "meet", "calendar", "workflows", "gemini_app"}

# OU -> (share of headcount, adoption propensity, preferred (app, feature, action) combos)
W = ("gmail", "help_me_write", "generate_text")
NOTES = ("meet", "take_notes_for_me", "classic_use_case_meet_take_notes_for_me_session")
OUS = {
    "/Leadership":            (0.04, 1.4, [W, ("gmail", "side_panel", "summarize"), ("docs", "side_panel", "summarize_file"), NOTES]),
    "/Sales/APAC":            (0.12, 1.2, [W, ("gmail", "help_me_refine", "formalize"), NOTES,
                                           ("calendar", "help_me_schedule", "suggest_time")]),
    "/Sales/EMEA":            (0.10, 0.9, [W, ("gmail", "side_panel", "summarize"), NOTES]),
    "/Sales/Americas":        (0.10, 0.7, [W, ("docs", "help_me_write", "generate_document")]),
    "/Marketing":             (0.09, 1.5, [("docs", "help_me_visualize", "generate_image_for_current_page"),
                                           ("docs", "help_me_write", "generate_document"), ("docs", "help_me_refine", "paraphrase"),
                                           ("gemini_app", "chat_with_gemini", "conversation")]),
    "/Engineering/Platform":  (0.13, 1.1, [("sheets", "ai_function", "generate_ai_function_response"), ("gemini_app", "chat_with_gemini", "conversation"),
                                           ("docs", "side_panel", "summarize_file"), ("workflows", "workflows_execution", "")]),
    "/Engineering/Product":   (0.12, 1.0, [("gemini_app", "chat_with_gemini", "conversation"), ("docs", "help_me_write", "generate_document"),
                                           ("drive", "side_panel", "summarize_drive_homepage_doclist_files"), NOTES]),
    "/Finance":               (0.07, 0.6, [("sheets", "enhanced_smart_fill", "classic_use_case_sheets_turbofill"),
                                           ("sheets", "ai_function", "generate_ai_function_response"), ("gmail", "side_panel", "summarize")]),
    "/People":                (0.06, 0.8, [W, ("docs", "help_me_refine", "proofread"), ("calendar", "help_me_schedule", "suggest_time")]),
    "/Operations":            (0.08, 0.5, [("calendar", "help_me_schedule", "suggest_time"), ("sheets", "ai_function", "generate_ai_function_response"),
                                           ("workflows", "workflows_creation", "")]),
    "/Customer Success":      (0.09, 1.0, [W, ("gmail", "side_panel", "summarize"), ("drive", "side_panel", "summarize_file"),
                                           ("gemini_app", "chat_with_gemini", "search_web")]),
}
COMMON = [W, ("gmail", "side_panel", "summarize"), ("docs", "side_panel", "summarize_file"),
          ("gemini_app", "chat_with_gemini", "conversation"), ("docs", "help_me_refine", "proofread"), NOTES]
assert all(c[0] in TESTED_APPS for v in OUS.values() for c in v[2]) and all(c[0] in TESTED_APPS for c in COMMON)


def build(n_users: int, days: int, seed: int, tz_name: str = "Asia/Singapore"):
    from zoneinfo import ZoneInfo
    rng = random.Random(seed)
    tz = ZoneInfo(tz_name)
    end = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(days=days)

    names, seen = [], set()
    while len(names) < n_users:
        f, l = rng.choice(FIRST), rng.choice(LAST)
        if (f, l) not in seen:
            seen.add((f, l))
            names.append((f, l))
    ou_names, weights = list(OUS), [v[0] for v in OUS.values()]
    users, activities = [], []
    for f, l in names:
        ou = rng.choices(ou_names, weights)[0]
        email = f"{f}.{l}@lumenfield-demo.example".lower()
        users.append({"primaryEmail": email, "name": {"fullName": f"{f} {l}"}, "orgUnitPath": ou,
                      "suspended": rng.random() < 0.015})
        _, propensity, combos = OUS[ou]
        # Some people never use Gemini; the rest have a log-normal appetite and a start day
        # (adoption grows over the window), and a few lapse towards the end.
        if rng.random() > min(0.92, 0.45 + 0.25 * propensity):
            continue
        rate = math.exp(rng.gauss(-0.3, 1.0)) * propensity
        first_day = int(days * min(0.85, rng.random() ** 2.2)) if rng.random() < 0.6 else 0
        lapse_day = rng.randrange(int(days * 0.5), days) if rng.random() < 0.12 else days
        mix = combos * 3 + COMMON + rng.sample(COMMON + [c for v in OUS.values() for c in v[2]], 3)
        for d in range(first_day, lapse_day):
            day = start + timedelta(days=d)
            local = day.astimezone(tz)
            weekday = local.weekday()
            ramp = min(1.0, 0.35 + 0.65 * (d - first_day + 1) / 21)   # people use it more once they start
            lam = rate * ramp * (1.0 if weekday < 5 else 0.12)
            for _ in range(_poisson(rng, lam)):
                hour = min(23, max(0, int(rng.gauss(13, 3.2))))
                t = local.replace(hour=hour, minute=rng.randrange(60)).astimezone(timezone.utc)
                if t > end:
                    continue
                app, feat, act = rng.choice(mix)
                activities.append({
                    "id": {"time": t.strftime("%Y-%m-%dT%H:%M:%S.000Z"), "applicationName": "gemini_in_workspace_apps"},
                    "actor": {"email": email},
                    "events": [{"type": "ai_usage_event", "name": "feature_utilization", "parameters": [
                        {"name": "app_name", "value": app}, {"name": "feature_source", "value": feat},
                        {"name": "action", "value": act},
                        {"name": "event_category", "value": rng.choice(["active_generate", "active_summarize", "active_conversations"])}]}],
                })
    fmt = "%Y-%m-%dT%H:%M:%S.000Z"
    return activities, users, start.strftime(fmt), end.strftime(fmt)


def _poisson(rng, lam):
    if lam <= 0:
        return 0
    L, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= L:
            return k
        k += 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--users", type=int, default=320)
    ap.add_argument("--days", type=int, default=120)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--timezone", default="Asia/Singapore")
    ap.add_argument("--company", default="Lumenfield Group (demo)")
    ap.add_argument("--out", default=str(BASE / "demo_site"))
    a = ap.parse_args(argv)

    activities, users, start, end = build(a.users, a.days, a.seed, a.timezone)
    cfg = {**DEFAULTS, "timezone": a.timezone, "window_days": a.days, "min_group_size": 5,
           "target_adoption_pct": 60, "_start": start, "_end": end}
    events = transform.flatten(activities)
    summary = transform.build_user_summary(events, users, usage_event_names=cfg["usage_event_names"], tz=a.timezone)
    scoped = transform.scoped_events(events, summary, cfg["usage_event_names"])
    payload = cio.build_payload(summary, scoped, cfg)
    payload["meta"]["demo"] = True
    out = Path(a.out)
    page = cio.render(out, "demo", payload, title=f"Gemini adoption · {a.company}")
    index = out / "index.html"
    page.replace(index)
    (out / "cio_dashboard_demo.html").unlink(missing_ok=True)
    print(f"{len(users)} fictional people, {len(scoped):,} Gemini actions over {a.days} days")
    print(f"wrote {index}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
